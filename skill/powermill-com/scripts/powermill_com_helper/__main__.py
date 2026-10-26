from pathlib import Path
import argparse,json,sys
from .core import Executor,RunStore,HelperError,validate_plan,atomic_json,file_lease
from .planner import create_bundle,capture_sort_state,replan_bundle
from .transport import PowerMillTransport
from .surface import capture_profile,audit_geometry,create_surface_bundle

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def main(argv=None):
    parser=argparse.ArgumentParser(description='Direct PowerMill COM helper; planning and status are offline')
    sub=parser.add_subparsers(dest='action',required=True)
    validate=sub.add_parser('validate');validate.add_argument('plan')
    status=sub.add_parser('status');status.add_argument('run_dir')
    session=sub.add_parser('session');session.add_argument('--connect',action='store_true');session.add_argument('--output')
    surface=sub.add_parser('capture-surface');surface.add_argument('guard');surface.add_argument('name');surface.add_argument('output');surface.add_argument('--connect',action='store_true');surface.add_argument('--geometry',action='store_true');surface.add_argument('--max-full-points',type=int,default=10000);surface.add_argument('--max-full-passes',type=int,default=100);surface.add_argument('--sample-passes',type=int,default=32);surface.add_argument('--audit-budget',type=float,default=120)
    surface_plan=sub.add_parser('plan-surface');surface_plan.add_argument('input');surface_plan.add_argument('output');surface_plan.add_argument('--enable-execution',action='store_true')
    sort=sub.add_parser('plan-sort');sort.add_argument('input');sort.add_argument('output');sort.add_argument('--max-moves',type=int,default=64);sort.add_argument('--enable-execution',action='store_true')
    capture=sub.add_parser('capture-sort');capture.add_argument('input');capture.add_argument('output');capture.add_argument('--connect',action='store_true')
    replan=sub.add_parser('replan-sort');replan.add_argument('input');replan.add_argument('observed');replan.add_argument('output');replan.add_argument('--max-moves',type=int,default=64);replan.add_argument('--enable-execution',action='store_true')
    for name in ('run','reconcile'):
        p=sub.add_parser(name);p.add_argument('plan');p.add_argument('run_dir');p.add_argument('--connect',action='store_true')
        if name=='run':p.add_argument('--allow-write',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.action=='validate':
            plan=validate_plan(read(args.plan));result={'valid':True,'operations':len(plan['operations']),'offline_only':bool(plan.get('offline_only')),'connected':False}
        elif args.action=='status':
            directory=Path(args.run_dir)
            if not (directory/'state.json').is_file():raise HelperError('No saved run state')
            store=RunStore(directory,read(directory/'plan.json'));result=store.load()
        elif args.action=='session':
            if not args.connect:raise HelperError('Read-only session discovery requires --connect')
            transport=PowerMillTransport(connect=True)
            try:
                result=transport.session()
                if args.output:atomic_json(args.output,result)
            finally:transport.close()
        elif args.action=='capture-surface':
            if not args.connect:raise HelperError('Read-only profile capture requires --connect')
            guard=read(args.guard);transport=PowerMillTransport(connect=True)
            try:
                with file_lease(Path(__file__).parent/'session-locks'/('pid-'+str(guard['process_id'])+'.lock')):
                    result=capture_profile(args.name,transport,guard)
                    if args.geometry:result['geometry']=audit_geometry(args.name,transport,guard,max_full_points=args.max_full_points,max_full_passes=args.max_full_passes,max_sample_passes=args.sample_passes,budget_s=args.audit_budget)
                    atomic_json(args.output,result)
            finally:transport.close()
            result={'saved':args.output,'name':result['name'],'connected':True,'read_only':True}
        elif args.action=='plan-surface':result=create_surface_bundle(read(args.input),args.output,enable_execution=args.enable_execution)
        elif args.action=='plan-sort':
            data=read(args.input);result=create_bundle(data['layers'],data['guard'],args.output,max_moves=args.max_moves,enable_execution=args.enable_execution)
        elif args.action=='replan-sort':result=replan_bundle(read(args.input),read(args.observed),args.output,max_moves=args.max_moves,enable_execution=args.enable_execution)
        elif args.action=='capture-sort':
            if not args.connect:raise HelperError('Read-only live capture requires explicit --connect')
            data=read(args.input);transport=PowerMillTransport(connect=True)
            try:
                with file_lease(Path(__file__).parent/'session-locks'/('pid-'+str(data['guard']['process_id'])+'.lock')):
                    result=capture_sort_state(data,transport);atomic_json(args.output,result)
            finally:transport.close()
        else:
            plan=validate_plan(read(args.plan))
            if plan.get('offline_only'):raise HelperError('Archived/offline-only plan cannot connect or execute')
            if not args.connect:raise HelperError('Explicit --connect is required')
            if args.action=='run' and not args.allow_write:raise HelperError('Explicit --allow-write is required')
            transport=PowerMillTransport(connect=True)
            try:
                engine=Executor(plan,args.run_dir,transport)
                result=engine.run(allow_write=True) if args.action=='run' else engine.reconcile()
            finally:transport.close()
        print(json.dumps(result,ensure_ascii=True,indent=2));return 0
    except (HelperError,OSError,ValueError,KeyError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=True),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
