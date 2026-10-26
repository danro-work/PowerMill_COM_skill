"""Version admission must not weaken identity or offline execution gates."""
import copy,tempfile,unittest
from pathlib import Path
from powermill_com_helper.core import validate_plan,Executor,HelperError,NotReady
from .test_executor import plan,Fake

class VersionTests(unittest.TestCase):
    def test_supported_versions_execute_and_resume_without_replay(self):
        for version in (2023000,2023003,2023101):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as directory:
                p=plan();p['guard']['version']=version
                fake=Fake();fake.guard=copy.deepcopy(p['guard'])
                engine=Executor(p,Path(directory)/'run',fake,lock_dir=Path(directory)/'locks')
                engine.run(allow_write=True);engine.run(allow_write=True)
                self.assertEqual(fake.calls,['one'])
    def test_unknown_and_noninteger_versions_are_rejected(self):
        for version in (2023999,2024000,None,'2023101',True):
            with self.subTest(version=version):
                p=plan();p['guard']['version']=version
                with self.assertRaises(HelperError):validate_plan(p)
    def test_version_2023101_keeps_offline_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            p=plan();p['guard']['version']=2023101;fake=Fake();fake.guard=copy.deepcopy(p['guard'])
            engine=Executor(p,Path(directory)/'run',fake,lock_dir=Path(directory)/'locks')
            with self.assertRaises(HelperError):engine.run()
            self.assertEqual(fake.calls,[])
    def test_version_change_blocks_before_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            p=plan();p['guard']['version']=2023101;fake=Fake()
            engine=Executor(p,Path(directory)/'run',fake,lock_dir=Path(directory)/'locks')
            with self.assertRaises(NotReady):engine.run(allow_write=True)
            self.assertEqual(fake.calls,[])
