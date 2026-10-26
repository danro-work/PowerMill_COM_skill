"""Surface machining: read profiles, prepare copy-only plans, verify actual results.

No COM connection on import. Native creation is deliberately not certified by
the offline tests. Arbitrary new surface selection needs a recorded macro;
the tested read API never selects or changes model components.
"""
from pathlib import Path
import copy,hashlib,math,time,uuid,xml.etree.ElementTree as ET
from .core import HelperError,StateDrift,assert_ready,atomic_json,utcnow,validate_plan,xml_match
from .planner import safe_name,finite

PARAMETERS={
 'Stepover':('number',None),'Thickness':('number',None),'Tolerance':('number',None),
 'ProjectionPatternDirection':('enum',{'u','v'}),
 'ProjectionSurfaceUnits':('enum',{'distance'}),
 'PatternStyle':('enum',{'one_way','two_way'}),
 'StartCorner':('enum',{'umin_vmin','umin_vmax','umax_vmin','umax_vmax'}),
 'SurfaceSide':('enum',{'inside','outside'}),'Spiral':('bool',None),
 'ToolAxis.Type':('enum',{'lead_lean'}),
 'ToolAxis.LeadLeanMode':('enum',{'contact_normal','vertical','frame_normal','tangent_normal'}),
 'ToolAxis.Lead':('number',None),'ToolAxis.Lean':('number',None),
 'Connections.MoveStartPoints':('bool',None),
}
SELECTION_KINDS=('default','surfaces')

def entity(name):return "entity('toolpath','"+safe_name(name)+"')"
def canonical_hash(raw):
    try:return hashlib.sha256(ET.canonicalize(raw,strip_text=True).encode('utf-8')).hexdigest()
    except ET.ParseError as exc:raise HelperError('Malformed surface settings XML') from exc
def number(value):return finite(value)
def scalar(raw):return ''.join(ET.fromstring(raw).itertext())
def read(transport,guard,expression):return transport.read_xml([expression],guard)[0]
def vector(node):
    if node is None or len(node)!=3:raise HelperError('Missing point vector')
    return [number(x.text) for x in node]
def children(raw,count):
    node=ET.fromstring(raw)
    if node.tag!='array' or len(node)!=count:raise HelperError('Surface array response count mismatch')
    return list(node)
def _settings(raw):
    root=ET.fromstring(raw)
    if root.tag!='toolpath' or root.findtext('Strategy')!='surface_machine':raise HelperError('A surface_machine template is required')
    return {key:root.findtext(key.replace('.','/')) for key in PARAMETERS}

def normalize_selection(selection):
    result=[];seen=set()
    for row in selection:
        kind=row.get('kind');index=row.get('index')
        if kind not in SELECTION_KINDS or type(index) is not int or not 0<=index<18 or (kind,index) in seen:raise HelperError('Invalid/duplicate surface set')
        seen.add((kind,index));members=[]
        for member in row.get('members',[]):
            if set(member)!={'model','name'}:raise HelperError('Surface membership needs explicit model and component names')
            members.append({'model':safe_name(member['model']),'name':safe_name(member['name'])})
        if len({(m['model'],m['name']) for m in members})!=len(members):raise HelperError('Duplicate surface component')
        result.append({'kind':kind,'index':index,'members':sorted(members,key=lambda x:(x['model'],x['name']))})
    if seen!={(k,i) for k in SELECTION_KINDS for i in range(18)}:raise HelperError('All default/surfaces sets 0..17 must be captured, including empty sets')
    return sorted(result,key=lambda x:(x['kind'],x['index']))

def capture_membership(name,transport,guard):
    e=entity(name);rows=[]
    for kind in SELECTION_KINDS:
        counts=children(read(transport,guard,'{'+','.join(f"size(model_components_thicknessset({e},'{kind}',{i}))" for i in range(18))+'}'),18)
        for i,node in enumerate(counts):
            n=int(number(''.join(node.itertext())));members=[]
            if n:
                raw=read(transport,guard,f"components(model_components_thicknessset({e},'{kind}',{i}))")
                members=[{'model':x.findtext('Model'),'name':x.findtext('Name')} for x in children(raw,n)]
            rows.append({'kind':kind,'index':i,'members':members})
    return normalize_selection(rows)

def capture_profile(name,transport,guard):
    """Only reads, including persisted sets rather than current GUI selection."""
    assert_ready(transport.probe(guard),guard);e=entity(name)
    raw=read(transport,guard,e);settings=_settings(raw)
    result={'schema':1,'guard':copy.deepcopy(guard),'name':name,'entity_id':scalar(read(transport,guard,e+'.ID')),
        'captured_at':utcnow(),'settings_xml':raw,'settings_hash':canonical_hash(raw),'parameters':settings,
        'folder':scalar(read(transport,guard,f'pathname({e})')),
        'selection':capture_membership(name,transport,guard),'read_only':True}
    if canonical_hash(read(transport,guard,e))!=result['settings_hash']:raise StateDrift('Template changed while capturing profile')
    assert_ready(transport.probe(guard),guard)
    return result

def audit_geometry(name,transport,guard,*,max_full_points=10000,max_full_passes=100,center=(0,0),max_sample_passes=32,budget_s=120,require_full=False):
    """Full coordinates for small paths; bounded sample otherwise, PP kept separate."""
    if type(max_full_points) is not int or max_full_points<0 or type(max_full_passes) is not int or max_full_passes<0:raise HelperError('Invalid audit limits')
    if type(max_sample_passes) is not int or max_sample_passes<2 or number(budget_s)<=0:raise HelperError('Invalid sampling/time budget')
    deadline=time.monotonic()+number(budget_s)
    def audit_read(expression):
        if time.monotonic()>=deadline:raise HelperError('Surface audit time budget exhausted; verification is incomplete')
        return read(transport,guard,expression)
    def sample_passes(count):return sorted({round(i*(count-1)/(min(count,max_sample_passes)-1)) for i in range(min(count,max_sample_passes))}) if count>1 else [0]
    if len(center)!=2:raise HelperError('Explicit XY center required')
    center=[number(x) for x in center];e=entity(name);assert_ready(transport.probe(guard),guard)
    before=canonical_hash(audit_read(e))
    n=int(number(scalar(audit_read(f"toolpath_component_count({e},'segments')"))))
    if not 0<n<=100000:raise HelperError('Unexpected surface pass count')
    if require_full and n>max_full_passes:raise HelperError('Full axis audit exceeds pass limit; no sampled pass-off')
    count_indices=list(range(n)) if n<=max_full_passes else sample_passes(n)
    counts={}
    for start in range(0,len(count_indices),64):
        keys=count_indices[start:start+64]
        values=children(audit_read('{'+','.join(f'segment_point_count({e},{i})' for i in keys)+'}'),len(keys))
        counts.update({i:int(number(''.join(node.itertext()))) for i,node in zip(keys,values)})
    if any(x<=0 for x in counts.values()):raise HelperError('Empty working pass')
    full=len(counts)==n and n<=max_full_passes and sum(counts.values())<=max_full_points
    if require_full and not full:raise HelperError('Full axis audit exceeds point limit; no sampled pass-off')
    indices_passes=list(range(n)) if full else sample_passes(n);curves=[];dots=[];axis_norm_errors=[]
    for i in indices_passes:
        count=counts[i]
        indices=list(range(count)) if full else sorted({0,count//4,count//2,3*count//4,count-1})
        points=[]
        for start in range(0,len(indices),64):
            keys=indices[start:start+64]
            for k,node in zip(keys,children(audit_read('{'+','.join(f'segment_get_point({e},{i},{j})' for j in keys)+'}'),len(keys))):
                pos=vector(node.find('Position'));axis=vector(node.find('ToolAxis'));orientation=vector(node.find('OrientationVector'))
                norm=math.sqrt(sum(v*v for v in axis));axis_norm_errors.append(abs(norm-1))
                radius=math.hypot(pos[0]-center[0],pos[1]-center[1])
                if norm==0 or radius<1e-8:raise HelperError('Undefined axis or radial direction')
                dots.append(((pos[0]-center[0])*axis[0]+(pos[1]-center[1])*axis[1])/radius/norm)
                points.append({'index':k,'position':pos,'axis':axis,'orientation':orientation})
        keys=sorted({0,count//2,count-1})
        pp=[{'index':k,'xml':ET.tostring(node,encoding='unicode')} for k,node in zip(keys,children(audit_read('{'+','.join(f'point_parameters({e},{i},{j})' for j in keys)+'}'),len(keys)))]
        tc=children(audit_read('{'+','.join(f"segment_point_count({e},{i},'{kind}')" for kind in ['lead_in','lead_out','link'])+'}'),3)
        curves.append({'index':i,'count':count,'closed_gap':math.dist(points[0]['position'],points[-1]['position']),'points':points,'pp_sample':pp,'transition_counts':{kind:int(number(''.join(node.itertext()))) for kind,node in zip(['lead_in','lead_out','link'],tc)}})
    if canonical_hash(audit_read(e))!=before:raise StateDrift('Settings changed during geometry audit')
    return {'schema':1,'name':name,'passes':n,'working_points':sum(counts.values()) if len(counts)==n else None,'sampled_passes':len(indices_passes),'sampled_pass_indices':indices_passes,'all_working_points_read':full,
        'coverage':'all working positions/axes' if full else 'five or fewer working positions/axes in bounded, evenly spaced pass sample',
        'pp_coverage':'first/middle/last working point in sampled passes','transition_coverage':'counts in sampled passes only; intermediate geometry/collisions not verified',
        'strict_surface_normals_verified':False,'radial_center':center,'radial_dot_min':min(dots),'axis_norm_error_max':max(axis_norm_errors),'curves':curves}

def validate_surface_check(check):
    safe_name(check['name']);normalize_selection(check['selection'])
    for key,val in check.get('parameters',{}).items():_parameter(key,val)
    if check.get('require_computed') not in (None,True,False):raise HelperError('Invalid computed condition')
    minimum=check.get('min_passes',0)
    if type(minimum) is not int or minimum<0:raise HelperError('Invalid minimum passes')
    if 'axis_contract' in check:
        c=check['axis_contract']
        required={'center_xy','min_radial_dot','require_full','max_full_points','max_full_passes'}
        if not required.issubset(c) or set(c)-required-{'max_sample_passes','budget_s'} or len(c['center_xy'])!=2:raise HelperError('Explicit axis audit contract required')
        for x in c['center_xy']:number(x)
        if not -1<=number(c['min_radial_dot'])<=1 or type(c['require_full']) is not bool:raise HelperError('Invalid radial-axis contract')
        for key in ['max_full_points','max_full_passes']:
            if type(c[key]) is not int or c[key]<0:raise HelperError('Invalid full-audit threshold')
        if type(c.get('max_sample_passes',32)) is not int or c.get('max_sample_passes',32)<2 or number(c.get('budget_s',120))<=0:raise HelperError('Invalid sample/time budget')
    if 'audit_path' in check and not Path(check['audit_path']).is_absolute():raise HelperError('Absolute audit output required')
    return check

def verify_surface(check,transport,guard):
    validate_surface_check(check);e=entity(check['name']);raw=read(transport,guard,e)
    settings=_settings(raw)
    for key,want in check.get('parameters',{}).items():
        mode='number' if PARAMETERS[key][0] in ('number','bool') else 'text'
        node=ET.Element('value');node.text=settings[key]
        if not xml_match(ET.tostring(node,encoding='unicode'),{'expected':want,'compare':mode,'tolerance':1e-7}):return False
    if capture_membership(check['name'],transport,guard)!=normalize_selection(check['selection']):return False
    if check.get('require_computed') is not None and (ET.fromstring(raw).findtext('Computed')=='1')!=check['require_computed']:return False
    if check.get('min_passes',0) and number(scalar(read(transport,guard,f"toolpath_component_count({e},'segments')")))<check['min_passes']:return False
    if 'axis_contract' in check:
        c=check['axis_contract'];audit=audit_geometry(check['name'],transport,guard,max_full_points=c['max_full_points'],max_full_passes=c['max_full_passes'],center=c['center_xy'],max_sample_passes=c.get('max_sample_passes',32),budget_s=c.get('budget_s',120),require_full=c['require_full'])
        if 'audit_path' in check:atomic_json(check['audit_path'],audit)
        if c['require_full'] and not audit['all_working_points_read']:raise HelperError('Full axis audit exceeds configured limits; no sampled pass-off')
        if audit['radial_dot_min']<c['min_radial_dot'] or audit['axis_norm_error_max']>1e-5:return False
    return True

def _parameter(key,value):
    if key not in PARAMETERS:raise HelperError('Unsupported surface parameter: '+str(key))
    kind,values=PARAMETERS[key]
    if kind=='enum':
        if value not in values:raise HelperError('Invalid surface enum: '+key)
        return value
    if isinstance(value,bool):raise HelperError('Use explicit numeric 0/1, not booleans')
    n=number(value)
    if key in ('Stepover','Tolerance') and n<=0:raise HelperError('Stepover and tolerance must be positive')
    if kind=='bool' and n not in (0,1):raise HelperError('Expected numeric 0/1')
    if key.startswith('ToolAxis.') and abs(n)>180:raise HelperError('Axis angle outside -180..180')
    return format(n,'.15g')

def create_surface_bundle(request,directory,*,enable_execution=False):
    """Clone a prepared surface template. Never operates on the template itself.

    Optional recorded selection macro must already name this exact destination.
    Its resulting membership is checked before calculation; no speculative
    commands for interactive THICKNESS list selection are emitted here.
    """
    profile=copy.deepcopy(request['template']);source=safe_name(profile['name']);target=safe_name(request['target'])
    if target==source:raise HelperError('Surface result must be a separate copy')
    if profile.get('schema')!=1 or canonical_hash(profile['settings_xml'])!=profile['settings_hash']:raise HelperError('Invalid captured template hash')
    _settings(profile['settings_xml']);selection=normalize_selection(profile['selection'])
    folder=safe_name(request['folder'])
    if not folder.startswith('Toolpath\\') or folder.endswith('\\'):raise HelperError('Explicit destination Toolpath subfolder required')
    parent,leaf=folder.rsplit('\\',1)
    parameters={k:_parameter(k,v) for k,v in request['parameters'].items()}
    required={'Stepover','Thickness','Tolerance','ProjectionPatternDirection','ProjectionSurfaceUnits','PatternStyle','StartCorner','SurfaceSide','ToolAxis.Type','ToolAxis.LeadLeanMode','ToolAxis.Lead','ToolAxis.Lean','Spiral','Connections.MoveStartPoints'}
    if not required.issubset(parameters):raise HelperError('Surface settings must be explicit; do not silently inherit job policy')
    root=Path(directory).resolve()
    if root.exists():raise HelperError('Use a new surface bundle directory')
    # Validate all input before any artifact is written.
    guard=profile['guard'];src=entity(source);dst=entity(target);ops=[];artifacts=[]
    source_check={'expression':src,'compare':'xml_sha256','expected':profile['settings_hash'],'label':'Original template unchanged'}
    source_id={'expression':src+'.ID','expected':safe_name(profile['entity_id'])}
    absent={'expression':f"entity_exists('toolpath','{target}')",'expected':0,'compare':'number'}
    exists={'expression':f"entity_exists('toolpath','{target}')",'expected':1,'compare':'number'}
    folder_exists={'expression':f"folder_exists('{folder}')",'expected':1,'compare':'number'}
    tool=ET.fromstring(profile['settings_xml']).findtext('Tool/Name');safe_name(tool)
    def stable():return [copy.deepcopy(source_id),copy.deepcopy(source_check)]
    def surface_checks(sel,params=None,computed=None):
        result={'kind':'surface','name':target,'selection':sel,'parameters':params or {}}
        if computed is not None:result['require_computed']=computed
        return result
    def add(label,lines,pre,post,timeout):
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<=0:raise HelperError('Positive operation timeout required')
        token=uuid.uuid4().hex;marker='PMCOM SURFACE '+label+' '+token
        macro=root/'macros'/(label+'.mac');receipt=root/'receipts'/(label+'-'+token+'.done')
        body=['function Main(){']+lines+['STRING finished = "'+token+'"',f'FILE OPEN "{receipt.as_posix()}" FOR WRITE AS sf_done', 'FILE WRITE $finished TO sf_done','FILE CLOSE sf_done','PRINT "'+marker+'"','}']
        content=('\r\n'.join(body)+'\r\n').encode('cp1251')
        artifact={'path':str(macro),'sha256':hashlib.sha256(content).hexdigest()}
        ops.append({'id':label,'checkpoint_key':target,'command':'MACRO "'+str(macro)+'"','artifact':artifact,'trust_macro':bool(request.get('trust_macros',False)),
            'completion_marker':marker,'timeout_s':timeout,'prechecks':stable()+pre,'postchecks':stable()+post+[{'kind':'receipt','path':str(receipt),'token':token}]})
        artifacts.append((macro,content))
    if request.get('create_folder',True):
        # Folder checkpoint is independent of later changes to target settings.
        add('folder',[f'CREATE FOLDER "{parent}" "{leaf}"'],[{'expression':f"folder_exists('{parent}')",'expected':1,'compare':'number'},{'expression':f"folder_exists('{folder}')",'expected':0,'compare':'number'},absent],[folder_exists],30)
        ops[-1]['checkpoint_key']='folder:'+folder
    identity_path=root/'receipts'/('target-id-'+uuid.uuid4().hex+'.txt')
    identity={'kind':'identity','expression':dst+'.ID','path':str(identity_path)}
    copy_lines=[f'STRING copy_name = new_entity_name(\'toolpath\',\'{source}\')',f'COPY TOOLPATH "{source}"',f'RENAME TOOLPATH $copy_name "{target}"',f'EDIT FOLDER "{folder}" INSERT "{target}" LAST',
        f'STRING result_id = {dst}.ID',f'FILE OPEN "{identity_path.as_posix()}" FOR WRITE AS sf_identity','FILE WRITE $result_id TO sf_identity','FILE CLOSE sf_identity']
    add('copy',copy_lines,[absent,folder_exists],[exists,identity,{'expression':f'pathname({dst})','expected':folder+'\\'+target},surface_checks(selection)],60)
    current_selection=selection
    if request.get('selection_macro'):
        external=request['selection_macro'];path=Path(external['path']).resolve()
        content=path.read_bytes()
        if external.get('target')!=target or hashlib.sha256(content).hexdigest()!=external.get('sha256'):raise HelperError('Selection macro target/hash must be pinned explicitly')
        expected=normalize_selection(external['expected_selection']);copy_path=root/'macros'/'recorded-selection.mac'
        if any(c in str(copy_path) for c in '\r\n\0"'):raise HelperError('Invalid native macro path')
        artifacts.append((copy_path,content))
        add('selection',[f'ACTIVATE TOOLPATH "{target}"',f'MACRO "{copy_path}"'],[exists,identity,surface_checks(selection)],[exists,identity,surface_checks(expected)],external.get('timeout_s',60))
        # Nested file is independently hashed by executor before dispatch.
        ops[-1]['dependencies']=[{'path':str(copy_path),'sha256':external['sha256']}]
        current_selection=expected
    lines=[f'ACTIVATE TOOLPATH "{target}"',f'ACTIVATE TOOL "{tool}"']
    lines.extend("EDIT PAR '"+k+"' '"+v+"'" for k,v in parameters.items())
    add('configure',lines,[exists,identity,surface_checks(current_selection)],[exists,identity,surface_checks(current_selection,parameters)],60)
    if 'axis_contract' not in request:raise HelperError('Specify the actual-axis audit contract explicitly')
    final=surface_checks(current_selection,parameters,True);final['min_passes']=1
    final['axis_contract']=request['axis_contract'];final['audit_path']=str(root/'surface-audit.json')
    add('calculate',[f'EDIT TOOLPATH "{target}" CALCULATE'],[exists,identity,surface_checks(current_selection,parameters)],[exists,identity,final],request.get('calculate_timeout_s',180))
    plan={'schema':1,'guard':guard,'offline_only':bool(request.get('example_only')) or not enable_execution,'native_surface_creation_tested':False,'operations':ops}
    validate_plan(plan)
    root.mkdir(parents=True);(root/'macros').mkdir();(root/'receipts').mkdir()
    for path,content in artifacts:path.write_bytes(content)
    atomic_json(root/'plan.json',plan);atomic_json(root/'surface-request.json',request)
    return {'plan':str(root/'plan.json'),'operations':len(ops),'offline_only':plan['offline_only'],'native_surface_creation_tested':False,'connected':False}
