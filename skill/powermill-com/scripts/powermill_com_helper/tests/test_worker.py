"""Exercise the real PowerShell protocol with an in-memory fake app, never COM."""
from pathlib import Path
import base64,os,unittest
from powermill_com_helper.transport import JsonLineProcess
from .test_executor import GUARD
from powermill_com_helper.core import HelperError

FAKE=r'''
function New-FakeApplication {
 $app=[pscustomobject]@{Version=2023000;Busy=$false;Debug=[pscustomobject]@{ProcessID=1234};Project=[pscustomobject]@{Models=@([pscustomobject]@{ID='MODEL'})};Value='before';HelpText="Commands available :`r`r`nACTIVATE`r`r`nCREATE`t`"Создать объекты`"`r`r`nDEACTIVATE"}
 $app|Add-Member -MemberType ScriptMethod -Name GetParameterXML -Value {
  param($expression)
  if($expression -eq 'project_pathname(0)'){return '<string></string>'}
  return ('<string>'+$this.Value+'</string>')
 }
 $app|Add-Member -MemberType ScriptMethod -Name DoCommandEx -Value {
  param($command,[ref]$output)
  if($command -eq 'HELP'){$output.Value=$this.HelpText;return 0}
  if($command -eq 'MAKE_BUSY'){$this.Busy=$true;$output.Value='';return 0}
  $this.Value='Папка СС';$output.Value='';return 0
 }
 return $app
}
'''

@unittest.skipUnless(os.name=='nt','PowerShell 5 protocol integration requires Windows')
class WorkerProtocolTests(unittest.TestCase):
    def channel(self,fixture=None,version=2023000):
        source=Path(__file__).resolve().parents[1].joinpath('worker.ps1').read_text(encoding='utf-8')
        # If the fake is ever bypassed, the worker errors rather than using COM.
        source=source.replace('GetActiveObject','COM_FORBIDDEN_IN_TEST').replace('$pm=$null','$pm=New-FakeApplication',1)
        if fixture:
            path=str(Path(__file__).with_name('fixtures')/fixture).replace("'","''")
            source=source.replace('$pm=New-FakeApplication',"$pm=New-FakeApplication; $pm.HelpText=[IO.File]::ReadAllText('"+path+"')",1)
        encoded=base64.b64encode((FAKE.replace('Version=2023000','Version='+str(version))+'\n'+source).encode('utf-16le')).decode('ascii')
        executable=str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
        return JsonLineProcess([executable,'-NoProfile','-NonInteractive','-STA','-EncodedCommand',encoded])
    def test_supported_versions_and_identity_guards(self):
        for version in (2023000,2023003,2023101):
            with self.subTest(version=version):
                channel=self.channel(version=version)
                guard={**GUARD,'version':version}
                try:
                    self.assertTrue(channel.request('probe',{'guard':guard},10)['root_ready'])
                    self.assertEqual(channel.request('read_xml',{'guard':guard,'expressions':['value']},10),['<string>before</string>'])
                    out=channel.request('execute',{'guard':guard,'operation':{'command':'SET_TEST_VALUE'}},10)
                    self.assertTrue(out['dispatched'])
                    for wrong in ({**guard,'version':2023999},{**guard,'version':2023003 if version!=2023003 else 2023000},{**guard,'process_id':999},{**guard,'model_ids':['OTHER']},{**guard,'project_path':'different'}):
                        out=channel.request('execute',{'guard':wrong,'operation':{'command':'MUST_NOT_DISPATCH'}},10)
                        self.assertFalse(out['dispatched'])
                finally:channel.close()
        channel=self.channel(version=2023999)
        try:
            with self.assertRaises(HelperError):channel.request('probe',{'guard':{**GUARD,'version':2023999}},10)
        finally:channel.close()

    def test_actual_archived_contexts_in_powershell_classifier(self):
        for fixture,want in [('help-root.txt',True),('help-style.txt',False)]:
            channel=self.channel(fixture)
            try:self.assertEqual(channel.request('probe',{'guard':GUARD},10)['root_ready'],want)
            finally:channel.close()
    def test_actual_powershell_probe_utf8_reads_and_before_dispatch_rejection(self):
        channel=self.channel()
        try:
            session=channel.request('session',{},10)
            self.assertEqual(session['process_id'],1234);self.assertEqual(session['model_ids'],['MODEL'])
            state=channel.request('probe',{'guard':GUARD},10)
            self.assertTrue(state['root_ready']);self.assertFalse(state['busy']);self.assertEqual(state['model_ids'],['MODEL'])
            self.assertEqual(channel.request('read_xml',{'guard':GUARD,'expressions':['value']},10),['<string>before</string>'])
            with self.assertRaises(HelperError):
                channel.request('read_xml',{'guard':GUARD,'expressions':['point_add_parameter(1,2)']},10)
            with self.assertRaises(HelperError):
                channel.request('read_xml',{'guard':GUARD,'expressions':['select_components(1)']},10)
            expression="components(model_components_thicknessset(entity('toolpath','P1_PODDERZHKA'),'surfaces',1))"
            self.assertEqual(channel.request('read_xml',{'guard':GUARD,'expressions':[expression]},10),['<string>before</string>'])
            out=channel.request('execute',{'guard':GUARD,'operation':{'command':'SET_TEST_VALUE'}},10)
            self.assertTrue(out['dispatched']);self.assertEqual(out['code'],0)
            self.assertEqual(channel.request('read_xml',{'guard':GUARD,'expressions':['value','value']},10),['<string>Папка СС</string>']*2)
            wrong={**GUARD,'process_id':999}
            out=channel.request('execute',{'guard':wrong,'operation':{'command':'UNEXPECTED'}},10)
            self.assertFalse(out['dispatched'])
            channel.request('execute',{'guard':GUARD,'operation':{'command':'MAKE_BUSY'}},10)
            state=channel.request('probe',{'guard':GUARD},10)
            self.assertTrue(state['busy']);self.assertFalse(state['root_ready'])
        finally:channel.close()
