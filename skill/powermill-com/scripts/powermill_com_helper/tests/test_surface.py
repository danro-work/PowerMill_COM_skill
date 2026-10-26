"""Surface workflow tests use synthetic geometry only; never real COM."""
from pathlib import Path
import copy,hashlib,json,math,re,tempfile,unittest,xml.etree.ElementTree as ET
from powermill_com_helper.surface import capture_profile,audit_geometry,create_surface_bundle,verify_surface,canonical_hash,normalize_selection
from powermill_com_helper.core import Executor,HelperError,StateDrift,validate_plan
from .test_executor import GUARD

PARAMS={'Stepover':0.6,'Thickness':0.1,'Tolerance':0.1,'ProjectionPatternDirection':'u','ProjectionSurfaceUnits':'distance','PatternStyle':'one_way','StartCorner':'umin_vmax','SurfaceSide':'outside','Spiral':0,'ToolAxis.Type':'lead_lean','ToolAxis.LeadLeanMode':'contact_normal','ToolAxis.Lead':0,'ToolAxis.Lean':90.1,'Connections.MoveStartPoints':1}

def xml(tag,text):
    node=ET.Element(tag);node.text=str(text);return ET.tostring(node,encoding='unicode')
def split_array(expression):
    out=[];depth=0;quote=None;start=0
    for i,c in enumerate(expression):
        if quote:
            if c==quote:quote=None
        elif c in "'\"":quote=c
        elif c in '({[':depth+=1
        elif c in ')}]':depth-=1
        elif c==',' and depth==0:out.append(expression[start:i]);start=i+1
    out.append(expression[start:]);return out

class SurfaceFake:
    def __init__(self):
        self.raw=ET.Element('toolpath');ET.SubElement(self.raw,'Strategy').text='surface_machine';ET.SubElement(self.raw,'Computed').text='1'
        for key,value in PARAMS.items():
            node=self.raw
            for name in key.split('.'):
                child=node.find(name)
                if child is None:child=ET.SubElement(node,name)
                node=child
            node.text=str(value)
        ET.SubElement(ET.SubElement(self.raw,'Tool'),'Name').text='Laser'
        self.selection=[{'kind':k,'index':i,'members':[]} for k in ['default','surfaces'] for i in range(18)]
        self.selection[19]['members']=[{'model':'DemoModel','name':'SurfaceA'}]
        self.counts=[9,9];self.flipped=False;self.calls=[]
    def probe(self,guard):return {**GUARD,'busy':False,'root_ready':True}
    def read_xml(self,expressions,guard):return [self.expression(e) for e in expressions]
    def expression(self,e):
        self.calls.append(e)
        if e.startswith('{'):
            return '<array>'+''.join(self.expression(x) for x in split_array(e[1:-1]))+'</array>'
        if e.endswith('.ID'):return xml('string','TEMPLATE-ID')
        if e.startswith('pathname('):return xml('string','Toolpath\\Prepared\\SURFACE_TEMPLATE')
        if e.startswith('toolpath_component_count('):return xml('int',len(self.counts))
        if e.startswith('segment_point_count('):
            m=re.search(r",(\d+)(?:,'(lead_in|lead_out|link)')?\)$",e)
            return xml('int',2 if m[2] else self.counts[int(m[1])])
        if e.startswith('size(model_components_thicknessset(') or e.startswith('components(model_components_thicknessset('):
            m=re.search(r",'(default|surfaces)',(\d+)\)\)$",e);row=next(r for r in self.selection if r['kind']==m[1] and r['index']==int(m[2]))
            if e.startswith('size('):return xml('int',len(row['members']))
            return '<array>'+''.join('<map>'+xml('Model',m['model'])+xml('Name',m['name'])+'</map>' for m in row['members'])+'</array>'
        if e.startswith('segment_get_point('):
            i,j=map(int,re.search(r',(\d+),(\d+)\)$',e).groups());angle=2*math.pi*j/8
            axis=[math.cos(angle),math.sin(angle),0]
            if self.flipped:axis=[-x for x in axis]
            def vec(tag,v):return '<'+tag+'>'+''.join(xml('real',x) for x in v)+'</'+tag+'>'
            return '<map>'+vec('Position',[(10+i)*math.cos(angle),(10+i)*math.sin(angle),i])+vec('ToolAxis',axis)+vec('OrientationVector',[0,0,0])+'</map>'
        if e.startswith('point_parameters('):return '<map/>'
        if re.fullmatch(r"entity\('toolpath','[^']+'\)",e):return ET.tostring(self.raw,encoding='unicode')
        raise AssertionError('Unexpected expression '+e)

class SurfaceTests(unittest.TestCase):
    def setUp(self):self.fake=SurfaceFake();self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def profile(self):return capture_profile('SURFACE_TEMPLATE',self.fake,GUARD)
    def request(self):return {'template':self.profile(),'target':'SURFACE_COPY','folder':'Toolpath\\SurfaceTrial','parameters':copy.deepcopy(PARAMS),'axis_contract':{'center_xy':[0,0],'min_radial_dot':0,'require_full':True,'max_full_points':10000,'max_full_passes':100}}
    def test_capture_persisted_membership_and_readonly_settings(self):
        p=self.profile();self.assertTrue(p['read_only']);self.assertEqual(p['entity_id'],'TEMPLATE-ID')
        self.assertEqual(p['parameters']['ToolAxis.Lean'],'90.1');self.assertEqual(len(p['selection']),36)
        self.assertEqual(next(x for x in p['selection'] if x['kind']=='surfaces' and x['index']==1)['members'][0]['name'],'SurfaceA')
    def test_small_geometry_is_full_with_actual_axes_and_separate_pp(self):
        a=audit_geometry('SURFACE_TEMPLATE',self.fake,GUARD)
        self.assertTrue(a['all_working_points_read']);self.assertEqual(a['working_points'],18);self.assertGreater(a['radial_dot_min'],0.9999)
        self.assertFalse(a['strict_surface_normals_verified']);self.assertLess(a['curves'][0]['closed_gap'],1e-10)
        self.assertFalse(any('segment_get_point' in e and 'point_parameters' in e for e in self.fake.calls))
    def test_bounded_sampling_is_labelled(self):
        a=audit_geometry('SURFACE_TEMPLATE',self.fake,GUARD,max_full_points=2)
        self.assertFalse(a['all_working_points_read']);self.assertEqual(len(a['curves'][0]['points']),5)
    def test_large_path_samples_passes_without_reading_every_count(self):
        self.fake.counts=[9]*23071
        a=audit_geometry('SURFACE_TEMPLATE',self.fake,GUARD,max_sample_passes=8)
        self.assertEqual(a['passes'],23071);self.assertEqual(a['sampled_passes'],8)
        self.assertIsNone(a['working_points']);self.assertEqual(a['sampled_pass_indices'][0],0);self.assertEqual(a['sampled_pass_indices'][-1],23070)
        self.assertLess(len(self.fake.calls),250)
    def test_full_audit_limit_and_time_budget_stop_before_unbounded_reads(self):
        from unittest.mock import patch
        self.fake.counts=[9]*23071
        with self.assertRaises(HelperError):audit_geometry('SURFACE_TEMPLATE',self.fake,GUARD,require_full=True)
        self.assertLess(len(self.fake.calls),5)
        with patch('powermill_com_helper.surface.time.monotonic',side_effect=[0,121]):
            with self.assertRaises(HelperError):audit_geometry('SURFACE_TEMPLATE',self.fake,GUARD,budget_s=120)
    def test_axis_contract_rejects_inward_and_insufficient_coverage(self):
        p=self.profile();check={'kind':'surface','name':p['name'],'parameters':PARAMS,'selection':p['selection'],'require_computed':True,'min_passes':1,'axis_contract':{'center_xy':[0,0],'min_radial_dot':0.1,'require_full':True,'max_full_points':100,'max_full_passes':20}}
        self.assertTrue(verify_surface(check,self.fake,GUARD));self.fake.flipped=True
        self.assertFalse(verify_surface(check,self.fake,GUARD));self.fake.flipped=False
        check['axis_contract']['max_full_points']=2
        with self.assertRaises(HelperError):verify_surface(check,self.fake,GUARD)
    def test_membership_or_parameter_change_fails_verification(self):
        p=self.profile();c={'kind':'surface','name':p['name'],'parameters':PARAMS,'selection':p['selection']}
        self.fake.raw.find('Stepover').text='0.7';self.assertFalse(verify_surface(c,self.fake,GUARD))
        self.fake.raw.find('Stepover').text='0.6';self.fake.selection[19]['members'][0]['name']='Other'
        self.assertFalse(verify_surface(c,self.fake,GUARD))
    def test_copy_plan_is_offline_with_durable_stages_and_no_interactive_form(self):
        out=create_surface_bundle(self.request(),self.root/'bundle');plan=json.loads(Path(out['plan']).read_text())
        self.assertTrue(plan['offline_only']);self.assertEqual([o['id'] for o in plan['operations']],['folder','copy','configure','calculate'])
        validate_plan(plan)
        for o in plan['operations']:
            code=Path(o['artifact']['path']).read_text(encoding='cp1251')
            self.assertIn('FILE CLOSE',code);self.assertNotIn('THICKNESS LIST UPDATE',code);self.assertNotIn('FORM THICKNESS',code)
            self.assertEqual(hashlib.sha256(Path(o['artifact']['path']).read_bytes()).hexdigest(),o['artifact']['sha256'])
        code=(self.root/'bundle/macros/configure.mac').read_text(encoding='cp1251')
        self.assertIn("'ToolAxis.Lean' '90.1'",code)
        with self.assertRaises(HelperError):Executor(plan,self.root/'run',self.fake).run(allow_write=True)
    def test_original_target_and_incomplete_settings_rejected_before_artifacts(self):
        r=self.request();r['target']=r['template']['name']
        with self.assertRaises(HelperError):create_surface_bundle(r,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())
        r=self.request();del r['parameters']['ToolAxis.Lean']
        with self.assertRaises(HelperError):create_surface_bundle(r,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())
    def test_changed_template_hash_and_unknown_parameter_rejected(self):
        r=self.request();r['template']['settings_xml']+='<bad/>'
        with self.assertRaises((HelperError,ET.ParseError)):create_surface_bundle(r,self.root/'bad')
        r=self.request();r['parameters']['UnsupportedParameter']='x'
        with self.assertRaises(HelperError):create_surface_bundle(r,self.root/'bad')
    def test_selection_macro_is_pinned_and_verified_before_calculation(self):
        r=self.request();macro=self.root/'recorded.mac';macro.write_bytes(b'// recorded test fixture')
        r['selection_macro']={'path':str(macro),'target':r['target'],'sha256':hashlib.sha256(macro.read_bytes()).hexdigest(),'expected_selection':r['template']['selection']}
        out=create_surface_bundle(r,self.root/'bundle');plan=json.loads(Path(out['plan']).read_text())
        step=next(o for o in plan['operations'] if o['id']=='selection')
        self.assertEqual(len(step['dependencies']),1);self.assertTrue(any(c.get('kind')=='surface' for c in step['postchecks']))
        r['selection_macro']['sha256']='0'*64
        with self.assertRaises(HelperError):create_surface_bundle(r,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())
    def test_empty_and_duplicate_membership_catalogs_are_not_accepted(self):
        with self.assertRaises(HelperError):normalize_selection([])
        s=self.profile()['selection'];s.append(s[0])
        with self.assertRaises(HelperError):normalize_selection(s)
    def test_example_cannot_be_enabled_and_required_audit_has_no_silent_default(self):
        r=self.request();r['example_only']=True
        out=create_surface_bundle(r,self.root/'example',enable_execution=True)
        self.assertTrue(out['offline_only'])
        r=self.request();del r['axis_contract']
        with self.assertRaises(HelperError):create_surface_bundle(r,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())

if __name__=='__main__':unittest.main()
