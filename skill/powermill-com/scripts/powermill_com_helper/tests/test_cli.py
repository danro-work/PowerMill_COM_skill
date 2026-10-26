from contextlib import redirect_stderr,redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import json,tempfile,unittest
from powermill_com_helper.__main__ import main
from .test_executor import plan

class CliTests(unittest.TestCase):
    def test_archived_plan_cannot_connect_even_with_live_flags(self):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'plan.json';p=plan();p['offline_only']=True;file.write_text(json.dumps(p))
            with patch('powermill_com_helper.__main__.PowerMillTransport',side_effect=AssertionError('COM must not be constructed')):
                with redirect_stderr(StringIO()):code=main(['run',str(file),str(Path(d)/'run'),'--connect','--allow-write'])
            self.assertEqual(code,2)
    def test_validate_does_not_construct_a_live_backend(self):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'plan.json';file.write_text(json.dumps(plan()))
            with patch('powermill_com_helper.__main__.PowerMillTransport',side_effect=AssertionError('COM must not be constructed')):
                with redirect_stdout(StringIO()):code=main(['validate',str(file)])
            self.assertEqual(code,0)
    def test_surface_planning_is_offline_and_capture_requires_explicit_connect(self):
        from .test_surface import SurfaceFake,PARAMS
        from .test_executor import GUARD
        from powermill_com_helper.surface import capture_profile
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);data={'template':capture_profile('SURFACE_TEMPLATE',SurfaceFake(),GUARD),'target':'NEW_SURFACE','folder':'Toolpath\\Trial','parameters':PARAMS,'axis_contract':{'center_xy':[0,0],'min_radial_dot':0,'require_full':True,'max_full_points':1000,'max_full_passes':100}}
            input=root/'request.json';input.write_text(json.dumps(data),encoding='utf-8')
            with patch('powermill_com_helper.__main__.PowerMillTransport',side_effect=AssertionError('Must stay offline')):
                with redirect_stdout(StringIO()):self.assertEqual(main(['plan-surface',str(input),str(root/'bundle')]),0)
                with redirect_stderr(StringIO()):self.assertEqual(main(['capture-surface','unused','NAME','unused']),2)
                with redirect_stderr(StringIO()):self.assertEqual(main(['session']),2)
