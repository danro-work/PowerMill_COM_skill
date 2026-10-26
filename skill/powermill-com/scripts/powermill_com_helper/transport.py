"""Isolated JSON-lines worker with an external deadline; no eager COM connection."""
from pathlib import Path
import json,os,queue,subprocess,threading,time,uuid
from .core import HelperError,validate_read_expression

class TransportTimeout(HelperError):pass
class ProtocolError(HelperError):pass

class JsonLineProcess:
    def __init__(self,command):
        self.command=command;self.process=None;self.responses=queue.Queue();self.poisoned=False;self.request_lock=threading.Lock()
    def _start(self):
        if self.process is not None:return
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        self.process=subprocess.Popen(self.command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            text=True,encoding='utf-8',bufsize=1,creationflags=flags)
        def reader():
            try:
                for line in self.process.stdout:
                    try:self.responses.put(json.loads(line.lstrip('\ufeff')))
                    except (ValueError,TypeError):self.responses.put({'protocol_error':'Worker emitted non-JSON output'})
            except (OSError,ValueError):pass
            finally:self.responses.put({'protocol_error':'Worker channel closed'})
        threading.Thread(target=reader,daemon=True).start()
    def request(self,method,payload,timeout_s):
        with self.request_lock:return self._request(method,payload,timeout_s)
    def _request(self,method,payload,timeout_s):
        if self.poisoned:raise ProtocolError('Worker is quarantined; create a new channel and reconcile first')
        deadline=time.monotonic()+timeout_s;self._start();request_id=str(uuid.uuid4())
        sent=queue.Queue()
        def writer():
            try:
                self.process.stdin.write(json.dumps({'id':request_id,'method':method,'payload':payload},ensure_ascii=False)+'\n');self.process.stdin.flush();sent.put(None)
            except (OSError,ValueError) as exc:sent.put(exc)
        threading.Thread(target=writer,daemon=True).start()
        try:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise queue.Empty
            error=sent.get(timeout=remaining)
            if error:raise error
            remaining=deadline-time.monotonic()
            if remaining<=0:raise queue.Empty
            response=self.responses.get(timeout=remaining)
        except queue.Empty as exc:
            self.poisoned=True;self.close()
            raise TransportTimeout('Worker deadline exceeded. PowerMill outcome is unknown; only the helper worker was stopped.') from exc
        except (OSError,ValueError) as exc:
            self.poisoned=True;self.close();raise ProtocolError('Worker connection lost') from exc
        if response.get('id')!=request_id:
            self.poisoned=True;self.close();raise ProtocolError(response.get('protocol_error','Unexpected response ID'))
        if not response.get('ok'):
            # execute returns its own dispatched flag so the executor can distinguish
            # a server-side guard rejection from an uncertain native operation.
            if method=='execute':return {'dispatched':response.get('dispatched',True),'code':-1,'output':response.get('error','Worker error'),'error':response.get('error','Worker error')}
            raise HelperError(response.get('error','Worker error'))
        return response['result']
    def close(self):
        process=self.process
        if process is None:return
        if process.poll() is None:
            # This is precisely the child we started, never the PowerMill process.
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
        for stream in (process.stdin,process.stdout):
            if stream:
                try:stream.close()
                except OSError:pass

class PowerMillTransport:
    def __init__(self,*,connect=False,read_timeout_s=60,powershell=None):
        if not connect:raise HelperError('Live COM requires an explicit connect=True')
        if os.name!='nt':raise HelperError('Direct PowerMill COM requires Windows')
        import base64
        script=Path(__file__).with_name('worker.ps1').read_text(encoding='utf-8')
        encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
        executable=powershell or str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe')
        self.channel=JsonLineProcess([executable,'-NoLogo','-NoProfile','-NonInteractive','-STA','-EncodedCommand',encoded])
        self.read_timeout_s=read_timeout_s
    def session(self):return self.channel.request('session',{},self.read_timeout_s)
    def probe(self,guard):return self.channel.request('probe',{'guard':guard},self.read_timeout_s)
    def read_xml(self,expressions,guard):
        expressions=[validate_read_expression(expression) for expression in expressions]
        return self.channel.request('read_xml',{'guard':guard,'expressions':expressions},self.read_timeout_s)
    def execute(self,op,guard):return self.channel.request('execute',{'guard':guard,'operation':op},op['timeout_s'])
    def close(self):self.channel.close()
