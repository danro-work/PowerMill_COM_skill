# Direct COM only. Started only by an explicit live-connect command.
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::InputEncoding=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$pm=$null
function Get-Application {
 if($null -eq $script:pm){
  if($env:PMCOM_TARGET_PID){
   Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class PowerMillRotBinding {
 [DllImport("ole32.dll")]
 public static extern int GetRunningObjectTable(uint reserved, out IRunningObjectTable rot);
 [DllImport("ole32.dll")]
 public static extern int CreateBindCtx(uint reserved, out IBindCtx ctx);
 public static object GetByPid(int pid) {
  IRunningObjectTable rot;
  IBindCtx ctx;
  GetRunningObjectTable(0, out rot);
  CreateBindCtx(0, out ctx);
  IEnumMoniker en;
  rot.EnumRunning(out en);
  var items = new IMoniker[1];
  string wanted = "!PowerMILL.Application:" + pid.ToString();
  while(en.Next(1, items, IntPtr.Zero) == 0) {
   string display;
   try { items[0].GetDisplayName(ctx, null, out display); } catch { continue; }
   if(!String.Equals(display, wanted, StringComparison.OrdinalIgnoreCase)) continue;
   object instance;
   rot.GetObject(items[0], out instance);
   return instance;
  }
  throw new InvalidOperationException("PowerMill PID not registered in ROT: " + pid.ToString());
 }
}
'@
   $script:pm=[PowerMillRotBinding]::GetByPid([int]$env:PMCOM_TARGET_PID)
  }else{$script:pm=[Runtime.InteropServices.Marshal]::GetActiveObject('PowerMill.Application')}
 }
 return $script:pm
}
function Get-Scalar([string]$expression){
 $raw=(Get-Application).GetParameterXML($expression)
 if([string]::IsNullOrEmpty($raw)){throw "Empty XML: $expression"}
 return ([xml]$raw).DocumentElement.InnerText
}
function Assert-ReadExpression([string]$expression){
 if([string]::IsNullOrWhiteSpace($expression) -or $expression -match '[\r\n\x00;]'){throw 'Invalid read-only expression'}
 $stripped=[regex]::Replace($expression,'"[^"]*"|''[^'']*''','')
 if($stripped -match '[''"]|(?<![=!<>])=(?!=)'){throw 'Invalid read-only expression syntax'}
 $allowed=@('entity','entity_exists','folder','folder_exists','pathname','project_pathname','size','extract','toolpath_component_count','segment_point_count','segment_get_point','segment_get_length','point_parameters','segment_has_leadin','segment_has_leadout','abs','min','max','components','model_components_thicknessset','smart_models','smart_levels','smart_colours','limits','number_selected','get_folders','active_folder','explorer_selected_entities','component_has_point_parameter')
 foreach($call in [regex]::Matches($stripped,'\b([A-Za-z_]\w*)\s*\(')){
  if($allowed -notcontains $call.Groups[1].Value){throw 'Expression function is not approved for read-only use'}
 }
}
function Get-Session {
 $app=Get-Application
 if($app.Busy){return [ordered]@{process_id=[int]$app.Debug.ProcessID;version=[int]$app.Version;busy=$true;model_ids=$null;project_path=$null}}
 $models=@();foreach($model in $app.Project.Models){$models+=[string]$model.ID}
 return [ordered]@{process_id=[int]$app.Debug.ProcessID;version=[int]$app.Version;busy=[bool]$app.Busy;model_ids=@($models);project_path=(Get-Scalar 'project_pathname(0)')}
}
function Assert-Identity($guard,$session){
 if($guard.version -notin @(2023000,2023003,2023101)){throw 'Only the verified PowerMill 2023 profile is supported'}
 if($session.process_id -ne $guard.process_id -or $session.version -ne $guard.version){throw 'Process/version identity mismatch'}
 if($session.busy){return}
 $expected=@($guard.model_ids|ForEach-Object{$_.ToLowerInvariant()}|Sort-Object)
 $actual=@($session.model_ids|ForEach-Object{$_.ToLowerInvariant()}|Sort-Object)
 if(($actual -join '|') -cne ($expected -join '|')){throw 'Project model identity mismatch'}
 if($null -ne $guard.PSObject.Properties['project_path']){
  if($session.project_path.Replace('/','\').ToLowerInvariant() -cne $guard.project_path.Replace('/','\').ToLowerInvariant()){throw 'Project pathname mismatch'}
 }
}
function Assert-Idle($guard){
 $session=Get-Session;Assert-Identity $guard $session
 if($session.busy){throw 'PowerMill is busy; no command dispatched'}
 return $session
}
function Root-Probe($guard){
 $session=Get-Session;Assert-Identity $guard $session
 if($session.busy){$session['root_ready']=$false;return $session}
 $output=$null;$code=(Get-Application).DoCommandEx('HELP',[ref]$output)
 $root=($code -eq 0 -and $output -match '(?im)^[ \t]*Commands available[ \t]*:[ \t\r]*$' -and $output -match '(?m)^[ \t]*ACTIVATE(?:[ \t]+[^\r\n]*)?[\r]*$' -and $output -match '(?m)^[ \t]*CREATE(?:[ \t]+[^\r\n]*)?[\r]*$' -and $output -match '(?m)^[ \t]*DEACTIVATE(?:[ \t]+[^\r\n]*)?[\r]*$')
 $session['root_ready']=[bool]$root;$session['context_output']=[string]$output
 return $session
}
while($null -ne ($line=[Console]::ReadLine())){
 $request=$null;$dispatched=$false
 try{
  $request=$line|ConvertFrom-Json
  $payload=$request.payload;$guard=$payload.guard
  switch($request.method){
   'session' {$result=Get-Session}
   'probe' {$result=Root-Probe $guard}
   'read_xml' {
    foreach($expression in $payload.expressions){Assert-ReadExpression ([string]$expression)}
    $null=Assert-Idle $guard;$values=New-Object 'Collections.Generic.List[string]'
    foreach($expression in $payload.expressions){
     $raw=(Get-Application).GetParameterXML([string]$expression)
     if([string]::IsNullOrEmpty($raw)){throw "Empty XML: $expression"}
     $values.Add([string]$raw)
    }
    $result=@($values.ToArray())
   }
   'execute' {
    $session=Root-Probe $guard
    if($session.busy -or -not $session.root_ready){throw 'Parser not ready; no command dispatched'}
    $op=$payload.operation;$watch=[Diagnostics.Stopwatch]::StartNew()
    $artifacts=@();if($null -ne $op.artifact){$artifacts+=@($op.artifact)}
    if($null -ne $op.dependencies){$artifacts+=@($op.dependencies)}
    foreach($artifact in $artifacts){
     $bytes=[IO.File]::ReadAllBytes($artifact.path);$sha=[Security.Cryptography.SHA256]::Create()
     try{$hash=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
     if($hash -cne $artifact.sha256){throw 'Macro changed after preflight'}
    }
    if($null -ne $op.artifact -and $op.command -cne ('MACRO "'+$op.artifact.path+'"')){throw 'Macro path mismatch'}
    if($op.trust_macro){
     foreach($artifact in $artifacts){
      $output=$null;$dispatched=$true;$code=(Get-Application).DoCommandEx(('TRUST FILE "'+$artifact.path+'"'),[ref]$output)
      if($code -ne 0 -or [string]$output -match '(?im)unknown command|invalid command|unexpected input|(^|\n)\s*(Ошибка|Error)\s*:'){throw ('Specific macro trust failed: '+$output)}
     }
    }
    $output=$null;$dispatched=$true;$code=(Get-Application).DoCommandEx([string]$op.command,[ref]$output)
    $result=[ordered]@{dispatched=$true;code=[int]$code;output=[string]$output;seconds=$watch.Elapsed.TotalSeconds}
   }
   default {throw 'Unknown worker method'}
  }
  $response=[ordered]@{id=$request.id;ok=$true;result=$result}
 }catch{
  $response=[ordered]@{id=$request.id;ok=$false;dispatched=$dispatched;error=$_.Exception.Message}
 }
 [Console]::WriteLine(($response|ConvertTo-Json -Depth 60 -Compress))
}
