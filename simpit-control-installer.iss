[Setup]
AppName=Simpit
AppVersion=0.2.0
AppPublisher=Sandeep
AppPublisherURL=https://github.com/mysands/simpit
AppSupportURL=https://github.com/mysands/simpit/issues
DefaultDirName={autopf}\Simpit
DefaultGroupName=Simpit
OutputDir=dist\installer
OutputBaseFilename=SimPitControlSetup
SetupIconFile=simpit_control\ui\Simpit Control.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "control"; Description: "Control (this machine manages slaves)"
Name: "slave";   Description: "Slave (this machine is managed remotely)"

[Components]
Name: "control"; Description: "Simpit Control"; Types: control; Flags: exclusive
Name: "slave";   Description: "Simpit Slave";   Types: slave;   Flags: exclusive

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Components: control

[Files]
Source: "dist\simpit-control.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: control
Source: "dist\simpit-slave.exe";   DestDir: "{app}"; Flags: ignoreversion; Components: slave

[Icons]
Name: "{group}\Simpit Control";         Filename: "{app}\simpit-control.exe"; Components: control
Name: "{group}\Simpit Slave";           Filename: "{app}\simpit-slave.exe";   Components: slave
Name: "{group}\Uninstall Simpit";       Filename: "{uninstallexe}"
; Gated X-Plane launcher (generated in code, ortho opt-in only): waits
; for the ortho mount drive to be served before starting the sim.
Name: "{group}\Launch X-Plane (wait for ortho)"; Filename: "{app}\launch_xplane.bat"; Components: slave; Check: OrthoSelected
Name: "{userdesktop}\Simpit Control"; Filename: "{app}\simpit-control.exe"; Components: control; Tasks: desktopicon

[Registry]
; Register slave to run at Windows startup (current user, no elevation needed)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "SimpitSlave"; \
    ValueData: """{app}\simpit-slave.exe"""; \
    Components: slave; Flags: uninsdeletevalue
; Ortho scenery mount helper at logon (only when the user opted in).
; Same HKCU Run mechanism as the slave itself: no elevation needed, and
; the mount console window stays visible so errors are readable.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "SimpitOrthoMount"; \
    ValueData: """{app}\ortho_mount.bat"""; \
    Components: slave; Check: OrthoSelected; Flags: uninsdeletevalue

[Run]
; Control: user-visible "Launch now" checkbox on the finished page.
Filename: "{app}\simpit-control.exe"; Description: "Launch Simpit Control"; Flags: nowait postinstall skipifsilent; Components: control
; Slave: launched silently in CurStepChanged(ssDone) so we can run
; the verification dialog before the finished page appears.

[Code]
var
  KeyPage:      TInputQueryWizardPage;
  IdentityPage: TInputQueryWizardPage;
  XPlanePage:   TInputQueryWizardPage;
  BackupPage:   TInputQueryWizardPage;
  OrthoOptPage:   TInputOptionWizardPage;
  OrthoPage:      TInputQueryWizardPage;
  OrthoMountPage: TInputQueryWizardPage;
  BackupEnableCheck: TNewCheckBox;
  BackupBrowseBtn:   TButton;

function IsSlaveInstall: Boolean;
begin
  Result := WizardIsComponentSelected('slave');
end;

function GetFileAttributesW(lpFileName: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function IsReparsePoint(const Path: String): Boolean;
// True when Path is a junction or directory symlink. Used so install
// and uninstall can tell a link apart from a real folder - a real
// Custom Scenery folder must never be deleted, only renamed.
var
  A: Cardinal;
begin
  A := GetFileAttributesW(Path);
  Result := (A <> $FFFFFFFF) and ((A and $400) <> 0);  // FILE_ATTRIBUTE_REPARSE_POINT
end;

procedure RemoveSlaveFirewallRules;
// Remove inbound firewall rules added during slave installation.
// Uses ShellExec runas so we get the required elevation even though the
// uninstaller runs without admin rights. Best-effort: we don't wait for
// completion so the uninstaller does not block on the UAC prompt.
var
  PSCmd: String;
  ResultCode: Integer;
begin
  PSCmd :=
    '-ExecutionPolicy Bypass -WindowStyle Hidden -NonInteractive -Command "' +
    'Remove-NetFirewallRule -DisplayName ''Simpit Slave'' -ErrorAction SilentlyContinue"';
  ShellExec('runas', 'powershell.exe', PSCmd, '', SW_HIDE, ewNoWait, ResultCode);
end;

procedure KillOurRcloneMount;
// Stop only rclone processes whose command line references our NAS
// remote - a blanket "taskkill /IM rclone.exe" would take down any
// unrelated rclone job (e.g. a backup sync) running on the machine.
var
  RC: Integer;
begin
  Exec('powershell.exe',
       '-NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process | ' +
       'Where-Object { $_.Name -eq ''rclone.exe'' -and ' +
       '$_.CommandLine -like ''*randhawanas*'' } | ' +
       'ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"',
       '', SW_HIDE, ewWaitUntilTerminated, RC);
end;

procedure RestoreCustomScenery;
// Undo the Custom Scenery redirect made by LinkCustomScenery: remove
// the link (only if it really is a reparse point - never delete a
// real folder), then restore what was there first: the ".pre-ortho"
// folder if one was backed up, else the prior link (e.g. a direct UNC
// link to the NAS) recorded at install time.
var
  Link, Backup, Prior: String;
  RC: Integer;
begin
  Link := '';
  if not RegQueryStringValue(HKCU, 'Software\Simpit',
                             'OrthoSceneryLink', Link) then Exit;
  if Link = '' then Exit;
  if DirExists(Link) and IsReparsePoint(Link) then
    RemoveDir(Link);
  Backup := '';
  Prior  := '';
  RegQueryStringValue(HKCU, 'Software\Simpit', 'OrthoSceneryBackup', Backup);
  RegQueryStringValue(HKCU, 'Software\Simpit', 'OrthoSceneryPriorLink', Prior);
  if (Backup <> '') and DirExists(Backup) and (not DirExists(Link)) then
    RenameFile(Backup, Link)
  else if (Prior <> '') and (not DirExists(Link)) then
    // Recreate the pre-install link. UNC targets need a symlink, and
    // symlink creation needs elevation - same UAC pattern as install.
    ShellExec('runas', 'cmd.exe',
              '/c mklink /D "' + Link + '" "' + Prior + '"',
              '', SW_HIDE, ewWaitUntilTerminated, RC);
  RegDeleteValue(HKCU, 'Software\Simpit', 'OrthoSceneryLink');
  RegDeleteValue(HKCU, 'Software\Simpit', 'OrthoSceneryBackup');
  RegDeleteValue(HKCU, 'Software\Simpit', 'OrthoSceneryPriorLink');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
  CacheDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    // Kill the slave process before files are removed so Windows
    // does not complain that simpit-slave.exe is locked.
    Exec('taskkill.exe', '/F /IM simpit-slave.exe', '',
         SW_HIDE, ewWaitUntilTerminated, ResultCode);
    // Also stop the ortho mount helper if present (window title match
    // for the console, then our rclone mount). Best-effort: nothing to
    // do on machines that skipped the ortho option.
    Exec('taskkill.exe', '/F /FI "WINDOWTITLE eq SimPit Ortho Mount*"', '',
         SW_HIDE, ewWaitUntilTerminated, ResultCode);
    KillOurRcloneMount;
    Sleep(500);
    // The VFS cache can hold 100+ GB, but re-priming it takes hours -
    // ask instead of silently leaving (or deleting) it. Keeping it
    // makes a later reinstall start warm; the breadcrumb is cleared
    // only on deletion so the next install can still find the cache.
    if RegQueryStringValue(HKCU, 'Software\Simpit', 'OrthoCacheDir', CacheDir) and
       (CacheDir <> '') and DirExists(CacheDir) then
      if MsgBox(
          'Delete the ortho scenery disk cache?' + #13#10 +
          '  ' + CacheDir + #13#10 + #13#10 +
          'It can be very large (tens to hundreds of GB), but re-downloading ' +
          'and re-priming it takes hours. Choose No to keep it for a future ' +
          'reinstall.',
          mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(CacheDir, True, True, True);
        RegDeleteValue(HKCU, 'Software\Simpit', 'OrthoCacheDir');
      end;
    // Put X-Plane's Custom Scenery back the way we found it. Done
    // after the mount is stopped so the link target is quiescent.
    RestoreCustomScenery;
    // Remove the code-generated ortho files so the app folder can be
    // fully deleted (the uninstaller only tracks [Files]-installed ones).
    DeleteFile(ExpandConstant('{app}\ortho_mount.bat'));
    DeleteFile(ExpandConstant('{app}\ortho_mount.log'));
    DeleteFile(ExpandConstant('{app}\launch_xplane.bat'));
    DeleteFile(ExpandConstant('{app}\rclone.exe'));
    // Remove the inbound firewall rules we added during install.
    RemoveSlaveFirewallRules;
  end;
end;

function KeyFilePath: String;
begin
  Result := ExpandConstant('{userappdata}\simpit-slave\simpit.key');
end;

function LogFilePath: String;
begin
  Result := ExpandConstant('{userappdata}\simpit-slave\agent.log');
end;

function IsValidIP(S: String): Boolean;
var
  i, DotCount, OctetVal, OctetLen: Integer;
  OctetStr: String;
  c: Char;
begin
  Result := False;
  if S = '' then Exit;
  DotCount := 0;
  OctetStr := '';
  for i := 1 to Length(S) + 1 do
  begin
    if i = Length(S) + 1 then c := '.'
    else c := S[i];
    if c = '.' then
    begin
      OctetLen := Length(OctetStr);
      if (OctetLen = 0) or (OctetLen > 3) then Exit;
      OctetVal := StrToIntDef(OctetStr, -1);
      if (OctetVal < 0) or (OctetVal > 255) then Exit;
      Inc(DotCount);
      OctetStr := '';
    end else if (c >= '0') and (c <= '9') then
      OctetStr := OctetStr + c
    else
      Exit;
  end;
  Result := (DotCount = 4);
end;

procedure OfferRollback;
var
  ResultCode: Integer;
begin
  if MsgBox(
      'The slave could not connect to Simpit Control.' + #13#10 + #13#10 +
      'Would you like to uninstall Simpit Slave and roll back all changes?' + #13#10 + #13#10 +
      'Click Yes to uninstall now, or No to keep the installation' + #13#10 +
      'and troubleshoot manually later.',
      mbConfirmation, MB_YESNO) = IDYES then
  begin
    Exec(ExpandConstant('{uninstallexe}'),
         '/SILENT /SUPPRESSMSGBOXES', '',
         SW_HIDE, ewWaitUntilTerminated, ResultCode);
    MsgBox(
      'Simpit Slave has been uninstalled.' + #13#10 + #13#10 +
      'When you are ready to try again, re-run this installer and make sure' + #13#10 +
      'Simpit Control is running on the master machine first.',
      mbInformation, MB_OK);
  end else
  begin
    MsgBox(
      'The installation will remain.' + #13#10 + #13#10 +
      'Once you resolve the issue, the slave will auto-register with' + #13#10 +
      'Simpit Control the next time it broadcasts (every 60 seconds).' + #13#10 + #13#10 +
      'You can also manually add this slave in Simpit Control using:' + #13#10 +
      '  Name: ' + IdentityPage.Values[0] + #13#10 +
      '  Host: (this machine''s IP address)',
      mbInformation, MB_OK);
  end;
end;

procedure ShowFirewallAllowApp;
// Open Windows Firewall and guide the user through the simpler
// "Allow an app" screen (7 steps, no Advanced Security needed).
// Called when the automatic elevated rule addition fails.
var
  AppExe: String;
  Msg: String;
  ResultCode: Integer;
begin
  AppExe := ExpandConstant('{app}\simpit-slave.exe');
  // Open the Windows Firewall control panel for the user so they are
  // already in the right place when they read the instructions.
  ShellExec('open', 'control.exe', 'firewall.cpl', '', SW_SHOW,
            ewNoWait, ResultCode);
  Msg := 'Windows Firewall has been opened for you.' + #13#10 + #13#10;
  Msg := Msg + 'Follow these steps to allow Simpit Slave:' + #13#10 + #13#10;
  Msg := Msg + 'Step 1:  In the window that just opened, click' + #13#10;
  Msg := Msg + '         "Allow an app or feature through' + #13#10;
  Msg := Msg + '         Windows Defender Firewall"' + #13#10;
  Msg := Msg + '         on the LEFT side of the screen.' + #13#10 + #13#10;
  Msg := Msg + 'Step 2:  Click the "Change settings" button near the top.' + #13#10;
  Msg := Msg + '         Click Yes if Windows asks for permission.' + #13#10 + #13#10;
  Msg := Msg + 'Step 3:  Click "Allow another app..." near the bottom.' + #13#10 + #13#10;
  Msg := Msg + 'Step 4:  Click "Browse..." in the dialog that opens.' + #13#10 + #13#10;
  Msg := Msg + 'Step 5:  Navigate to this file and select it:' + #13#10;
  Msg := Msg + '         ' + AppExe + #13#10;
  Msg := Msg + '         Then click Open.' + #13#10 + #13#10;
  Msg := Msg + 'Step 6:  Click "Add".' + #13#10 + #13#10;
  Msg := Msg + 'Step 7:  Find "simpit-slave" in the list.' + #13#10;
  Msg := Msg + '         Tick BOTH boxes: Private and Public.' + #13#10;
  Msg := Msg + '         Click OK.' + #13#10 + #13#10;
  Msg := Msg + 'When done, start Simpit Slave from the Start menu.';
  MsgBox(Msg, mbInformation, MB_OK);
end;

procedure TroubleshootSlave;
var
  LogFile: String;
  LogText: AnsiString;
  Msg: String;
begin
  LogFile := LogFilePath;

  // Wait a moment for the slave to write any startup errors.
  Sleep(2000);

  if not FileExists(LogFile) then
  begin
    Msg := 'The slave log file was not found.' + #13#10 + #13#10;
    Msg := Msg + 'This usually means the slave did not start at all.' + #13#10 + #13#10;
    Msg := Msg + 'Things to try:' + #13#10;
    Msg := Msg + '  1. Check your antivirus - it may have blocked simpit-slave.exe.' + #13#10;
    Msg := Msg + '  2. Disable Windows Defender SmartScreen and retry.' + #13#10;
    Msg := Msg + '  3. Run simpit-slave.exe manually from the install folder to see the error.';
    MsgBox(Msg, mbError, MB_OK);
    Exit;
  end;

  if not LoadStringFromFile(LogFile, LogText) then
    LogText := '';

  // Wrong key: slave received a message from Control but signature failed.
  if (Pos('signature mismatch', LogText) > 0) or (Pos('ProtocolError', LogText) > 0) then
  begin
    Msg := 'Wrong security key.' + #13#10 + #13#10;
    Msg := Msg + 'The slave is running but Control messages are failing the signature check.' + #13#10;
    Msg := Msg + 'The key you entered does not match the one in Simpit Control.' + #13#10 + #13#10;
    Msg := Msg + 'Fix: open Simpit Control, click Security, copy the exact key,' + #13#10;
    Msg := Msg + 'then uninstall and reinstall the slave with that key.';
    MsgBox(Msg, mbError, MB_OK);
    OfferRollback;
    Exit;
  end;

  // Port conflict: another app is using the same port.
  if (Pos('already in use', LogText) > 0) or (Pos('WinError 10048', LogText) > 0) then
  begin
    Msg := 'Port conflict.' + #13#10 + #13#10;
    Msg := Msg + 'The slave could not bind to its network port because another application is using it.' + #13#10 + #13#10;
    Msg := Msg + 'Fix: find and close the other application, or restart this machine,' + #13#10;
    Msg := Msg + 'then start the slave again from the Start menu.';
    MsgBox(Msg, mbError, MB_OK);
    OfferRollback;
    Exit;
  end;

  // Port reserved by Windows (Hyper-V / Docker Desktop / WSL2).
  // The slave logs "Diagnosis: reserved" when it detects this.
  // This LOOKS like a firewall error (same WinError 10013) but firewall
  // rules cannot fix it — the OS has claimed the port at the kernel level.
  if Pos('Diagnosis: reserved', LogText) > 0 then
  begin
    Msg := 'Windows has reserved port 49100 or 49101 for its own use.' + #13#10 + #13#10;
    Msg := Msg + 'This is NOT a firewall problem. Adding firewall rules will' + #13#10;
    Msg := Msg + 'not help. This happens when Hyper-V, Docker Desktop, or' + #13#10;
    Msg := Msg + 'WSL2 is installed — common on Windows 11.' + #13#10 + #13#10;
    Msg := Msg + 'FIX 1 - Restart this PC (try this first):' + #13#10;
    Msg := Msg + '  Restarting releases reserved ports.' + #13#10 + #13#10;
    Msg := Msg + 'FIX 2 - If restarting does not help:' + #13#10;
    Msg := Msg + '  Open Command Prompt as administrator, then type:' + #13#10;
    Msg := Msg + '    net stop winnat' + #13#10;
    Msg := Msg + '  This temporarily releases Hyper-V''s reserved ports.' + #13#10;
    Msg := Msg + '  Start Simpit Slave from the Start menu.' + #13#10 + #13#10;
    Msg := Msg + 'FIX 3 - Re-run the installer and choose different ports.' + #13#10;
    Msg := Msg + '  To see reserved ranges open Command Prompt (admin) and type:' + #13#10;
    Msg := Msg + '    netsh interface ipv4 show excludedportrange protocol=udp' + #13#10;
    Msg := Msg + '    netsh interface ipv4 show excludedportrange protocol=tcp';
    MsgBox(Msg, mbError, MB_OK);
    OfferRollback;
    Exit;
  end;

  // Firewall / permission: OS blocked the listener (and it is not a port
  // reservation — the slave did not log "Diagnosis: reserved").
  if (Pos('WinError 10013', LogText) > 0) or
     (Pos('socket bind failed', LogText) > 0) or
     (Pos('Permission denied', LogText) > 0) then
  begin
    MsgBox(
      'Windows Firewall blocked Simpit Slave from opening its network port.' + #13#10 + #13#10 +
      'The automatic firewall rule that was added during installation did not' + #13#10 +
      'take effect on this machine (this is more common on Windows 11).' + #13#10 + #13#10 +
      'Click OK and Windows Firewall will open automatically.' + #13#10 +
      'Follow the on-screen instructions to allow Simpit Slave.',
      mbError, MB_OK);
    ShowFirewallAllowApp;
    OfferRollback;
    Exit;
  end;

  // Log is clean - likely a network / reachability issue.
  Msg := 'The slave is running and the log looks clean - no errors detected.' + #13#10 + #13#10;
  Msg := Msg + 'Possible causes:' + #13#10;
  Msg := Msg + '  1. Simpit Control is not running on the master machine.' + #13#10;
  Msg := Msg + '  2. This machine and the master are on different networks or VLANs.' + #13#10;
  Msg := Msg + '  3. A firewall is blocking UDP/TCP ports 49100/49101.' + #13#10 + #13#10;
  Msg := Msg + 'Things to try:' + #13#10;
  Msg := Msg + '  - Make sure Simpit Control is open on the master.' + #13#10;
  Msg := Msg + '  - Ping this machine from the master: ping ' + GetEnv('COMPUTERNAME') + #13#10;
  Msg := Msg + '  - Temporarily disable the firewall on both machines to test.';
  MsgBox(Msg, mbInformation, MB_OK);
  OfferRollback;
end;

function EscapeJson(S: String): String;
// Return S safe for embedding inside a JSON double-quoted string.
// Escapes backslash, double-quote, and strips control characters (0x00-0x1F)
// so that a crafted slave name cannot inject extra JSON keys or break the
// config file that the agent reads on startup.
var
  i: Integer;
  R: String;
  O: Integer;
begin
  R := '';
  for i := 1 to Length(S) do
  begin
    O := Ord(S[i]);
    if O < 32 then
      // Strip control characters (newline, tab, etc.) entirely.
      // Leaving them would break the JSON structure or the file write.
      Continue
    else if S[i] = '\' then R := R + '\\'
    else if S[i] = '"' then R := R + '\"'
    else R := R + S[i];
  end;
  Result := R;
end;

procedure BrowseForKeyFile(Sender: TObject);
var
  FileName: String;
  FileText: AnsiString;
  Key: String;
begin
  FileName := '';
  if GetOpenFileName(
      'Select your simpit.key file',
      FileName,
      '',
      'Key files (*.key)|*.key|All files (*.*)|*.*',
      'key') then
  begin
    if not LoadStringFromFile(FileName, FileText) then
    begin
      MsgBox('Could not read the selected file.', mbError, MB_OK);
      Exit;
    end;
    Key := Trim(String(FileText));
    KeyPage.Values[0] := Key;
  end;
end;

procedure BrowseForXPlaneFolder(Sender: TObject);
var
  Folder: String;
begin
  Folder := XPlanePage.Values[0];
  if Folder = '' then
    Folder := 'C:\';
  if BrowseForFolder('Select the X-Plane installation folder', Folder, False) then
    XPlanePage.Values[0] := Folder;
end;

procedure BrowseForBackupFolder(Sender: TObject);
var
  Folder: String;
begin
  Folder := BackupPage.Values[0];
  if Folder = '' then
    Folder := 'C:\';
  if BrowseForFolder('Select the backup destination folder', Folder, True) then
    BackupPage.Values[0] := Folder;
end;

procedure BrowseForCacheDir(Sender: TObject);
var
  Folder: String;
begin
  Folder := OrthoMountPage.Values[3];
  if Folder = '' then
    Folder := 'C:\';
  if BrowseForFolder('Select the folder for the scenery disk cache ' +
                     '(pick a drive with room for the whole cache)',
                     Folder, True) then
    OrthoMountPage.Values[3] := Folder;
end;

procedure BackupEnableToggle(Sender: TObject);
// Grey the backup fields in and out with the opt-in checkbox so the
// skip state is visible at a glance instead of implied by blank fields.
begin
  BackupPage.Edits[0].Enabled := BackupEnableCheck.Checked;
  BackupPage.Edits[1].Enabled := BackupEnableCheck.Checked;
  BackupBrowseBtn.Enabled     := BackupEnableCheck.Checked;
end;

function FirstIPv4(Addresses: Variant): String;
// Return the first usable IPv4 from a WMI IPAddress string array.
// Skips IPv6 (contains ':'), loopback and APIPA. The fixed upper
// bound with try/except is deliberate: PascalScript has no clean way
// to read a variant array's length, so we probe until indexing throws.
var
  J: Integer;
  S: String;
begin
  Result := '';
  for J := 0 to 9 do
  begin
    try
      S := Addresses[J];
    except
      Exit;
    end;
    if (Pos(':', S) = 0) and (Pos('127.', S) <> 1) and
       (Pos('169.254.', S) <> 1) and (S <> '0.0.0.0') then
    begin
      Result := S;
      Exit;
    end;
  end;
end;

function GetLocalIPAddress(): String;
// Best-effort detection of this machine's IPv4 via WMI. Returns ''
// when nothing usable is found — the caller must treat the value as
// a default only, never as authoritative.
var
  WbemLocator, WbemServices, ObjectSet: Variant;
  I: Integer;
begin
  Result := '';
  try
    WbemLocator := CreateOleObject('WbemScripting.SWbemLocator');
    WbemServices := WbemLocator.ConnectServer('.', 'root\CIMV2');
    ObjectSet := WbemServices.ExecQuery(
      'SELECT IPAddress FROM Win32_NetworkAdapterConfiguration ' +
      'WHERE IPEnabled = TRUE');
    for I := 0 to ObjectSet.Count - 1 do
    begin
      Result := FirstIPv4(ObjectSet.ItemIndex(I).IPAddress);
      if Result <> '' then Exit;
    end;
  except
    Result := '';
  end;
end;

// ── Ortho scenery cache mount (optional slave feature) ──────────────

function OrthoSelected: Boolean;
// True when this is a slave install and the user opted in to the ortho
// cache mount. Used as Check: on the Run-key registry entry and to gate
// the post-install setup steps.
begin
  Result := IsSlaveInstall and (OrthoOptPage <> nil) and OrthoOptPage.Values[0];
end;

function FindRclone: String;
// Locate rclone.exe. winget puts it in different places depending on
// who ran the install: user scope -> <profile>\AppData\...\WinGet\Links,
// machine scope (elevated shell) -> C:\Program Files\WinGet\Links. A
// just-finished install also updates PATH only in the registry, not in
// this already-running process, so the registry PATH is checked too.
// Empty string when nothing is found.
var
  FR: TFindRec;
  RegPath: String;
begin
  // A previous install's pinned copy - the one place a prior SimPit
  // setup GUARANTEED a working rclone, and it survives even if the
  // original winget/manual install it was copied from is gone.
  Result := AddBackslash(WizardDirValue) + 'rclone.exe';
  if FileExists(Result) then Exit;
  // Current user's winget shim (user-scope install).
  Result := ExpandConstant('{localappdata}\Microsoft\WinGet\Links\rclone.exe');
  if FileExists(Result) then Exit;
  // Machine-scope winget shim locations (elevated winget install).
  Result := 'C:\Program Files\WinGet\Links\rclone.exe';
  if FileExists(Result) then Exit;
  Result := 'C:\ProgramData\Microsoft\WinGet\Links\rclone.exe';
  if FileExists(Result) then Exit;
  // PATH as this process inherited it.
  Result := FileSearch('rclone.exe', GetEnv('PATH'));
  if Result <> '' then Exit;
  // PATH as the registry has it right now (fresh installs land here).
  if RegQueryStringValue(HKLM,
      'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
      'Path', RegPath) then
  begin
    Result := FileSearch('rclone.exe', RegPath);
    if Result <> '' then Exit;
  end;
  if RegQueryStringValue(HKCU, 'Environment', 'Path', RegPath) then
  begin
    Result := FileSearch('rclone.exe', RegPath);
    if Result <> '' then Exit;
  end;
  // Any other user profile's winget shim (installed under a different
  // account). Note: using it may hit profile ACLs, which is why
  // SetupOrthoMount copies the exe into {app} rather than running it
  // from here.
  if FindFirst('C:\Users\*', FR) then
  begin
    try
      repeat
        if (FR.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0) and
           (FR.Name <> '.') and (FR.Name <> '..') then
        begin
          Result := 'C:\Users\' + FR.Name +
                    '\AppData\Local\Microsoft\WinGet\Links\rclone.exe';
          if FileExists(Result) then Exit;
        end;
      until not FindNext(FR);
    finally
      FindClose(FR);
    end;
  end;
  // Common manual locations.
  Result := 'C:\rclone\rclone.exe';
  if FileExists(Result) then Exit;
  Result := ExpandConstant('{commonpf64}\rclone\rclone.exe');
  if FileExists(Result) then Exit;
  Result := '';
end;

procedure BrowseForRclone(Sender: TObject);
var
  FileName: String;
begin
  FileName := '';
  if GetOpenFileName('Locate rclone.exe', FileName, '',
      'rclone.exe|rclone.exe|Programs (*.exe)|*.exe', 'exe') then
    OrthoMountPage.Values[2] := FileName;
end;

function WinFspInstalled: Boolean;
// rclone mount needs the WinFsp driver; its dll location is stable.
begin
  Result := FileExists(ExpandConstant('{commonpf32}\WinFsp\bin\winfsp-x64.dll'))
         or FileExists(ExpandConstant('{commonpf64}\WinFsp\bin\winfsp-x64.dll'));
end;

function WingetPath: String;
begin
  Result := ExpandConstant('{localappdata}\Microsoft\WindowsApps\winget.exe');
  if not FileExists(Result) then
    Result := 'winget';
end;

procedure EnsureOrthoPrereqs;
// Called when leaving the opt-in page with the box ticked. Offers a
// winget install for anything missing; if the user declines or the
// install fails, the option is unticked and setup continues without
// the ortho mount - prerequisites never block the slave install itself.
var
  Missing: String;
  RC: Integer;
begin
  // Re-detect and surface the result on the settings page, so the user
  // can always SEE which rclone the installer found (and Browse to a
  // different one if detection picked wrong).
  if (Trim(OrthoMountPage.Values[2]) = '') or
     (not FileExists(Trim(OrthoMountPage.Values[2]))) then
    OrthoMountPage.Values[2] := FindRclone;
  Missing := '';
  if OrthoMountPage.Values[2] = '' then Missing := Missing + '  - rclone' + #13#10;
  if not WinFspInstalled then Missing := Missing + '  - WinFsp' + #13#10;
  if Missing = '' then Exit;
  if MsgBox(
      'The ortho scenery cache needs these programs which are not ' +
      'installed on this machine:' + #13#10 + #13#10 + Missing + #13#10 +
      'Install them now with winget?' + #13#10 +
      '(Windows may ask for administrator permission for WinFsp.)' + #13#10 + #13#10 +
      'Choose No to skip the ortho cache setup.',
      mbConfirmation, MB_YESNO) = IDYES then
  begin
    if OrthoMountPage.Values[2] = '' then
    begin
      Exec(WingetPath,
           'install --id Rclone.Rclone -e --accept-source-agreements ' +
           '--accept-package-agreements',
           '', SW_SHOW, ewWaitUntilTerminated, RC);
      OrthoMountPage.Values[2] := FindRclone;
    end;
    if not WinFspInstalled then
      Exec(WingetPath,
           'install --id WinFsp.WinFsp -e --accept-source-agreements ' +
           '--accept-package-agreements',
           '', SW_SHOW, ewWaitUntilTerminated, RC);
  end;
  if (OrthoMountPage.Values[2] = '') or (not WinFspInstalled) then
  begin
    MsgBox(
      'rclone and/or WinFsp are still missing, so the ortho cache setup ' +
      'will be skipped.' + #13#10 +
      'Install them and re-run this installer to add the mount later.',
      mbInformation, MB_OK);
    OrthoOptPage.Values[0] := False;
  end;
end;

procedure StopPriorOrthoMount;
// A previous install (or manual setup) may have left the mount helper
// running. The console window is identifiable by its title; beyond
// that, only rclone processes mounting our remote are stopped.
var
  RC: Integer;
begin
  Exec('taskkill.exe', '/F /FI "WINDOWTITLE eq SimPit Ortho Mount*"',
       '', SW_HIDE, ewWaitUntilTerminated, RC);
  KillOurRcloneMount;
  Sleep(500);
end;

procedure SetupOrthoMount;
// Post-install step: stop any prior helper, write the rclone remote
// (password is obscured by rclone itself), and generate the mount
// batch file that the Run key launches at every logon.
var
  RclonePath, Drive, Remote, CacheGB, CacheDir, PriorCache, Bat: String;
  RC: Integer;
begin
  RclonePath := Trim(OrthoMountPage.Values[2]);
  if not FileExists(RclonePath) then RclonePath := FindRclone;
  if RclonePath = '' then Exit;  // prereq step already warned + unticked

  StopPriorOrthoMount;

  // Copy rclone.exe into the app folder and run the mount from the
  // copy: the detected exe may live in ANOTHER user's profile (winget
  // run from an elevated/different account), which the logged-in user
  // cannot execute at logon. The copy also pins the version. Skipped
  // when the detected exe IS the pinned copy from a previous install.
  if CompareText(RclonePath, ExpandConstant('{app}\rclone.exe')) <> 0 then
    if FileCopy(RclonePath, ExpandConstant('{app}\rclone.exe'), False) then
      RclonePath := ExpandConstant('{app}\rclone.exe');

  // Skipped when the password is left blank: an existing rclone.conf
  // on this machine is then reused untouched.
  if OrthoPage.Values[3] <> '' then
  begin
    if (not Exec(RclonePath,
        'config create randhawanas smb' +
        ' "host=' + Trim(OrthoPage.Values[0]) + '"' +
        ' "user=' + Trim(OrthoPage.Values[2]) + '"' +
        ' "pass=' + OrthoPage.Values[3] + '"',
        '', SW_HIDE, ewWaitUntilTerminated, RC)) or (RC <> 0) then
      MsgBox(
        'Warning: the rclone NAS configuration could not be written ' +
        '(exit code ' + IntToStr(RC) + ').' + #13#10 +
        'The mount may fail to authenticate. Run "rclone config" in a ' +
        'terminal to fix it.',
        mbError, MB_OK);
  end;

  Drive    := UpperCase(Trim(OrthoMountPage.Values[0])) + ':';
  Remote   := 'randhawanas:' + Trim(OrthoPage.Values[1]);
  CacheGB  := Trim(OrthoMountPage.Values[1]);
  CacheDir := Trim(OrthoMountPage.Values[3]);
  // A bare drive root breaks the bat's cmd quoting ("D:\" escapes the
  // closing quote) and scatters cache files at top level - divert to a
  // subfolder. Longer paths get any trailing backslash stripped.
  if Length(CacheDir) <= 3 then
    CacheDir := AddBackslash(CacheDir) + 'rclone-cache';
  CacheDir := RemoveBackslashUnlessRoot(CacheDir);

  Bat := '@echo off' + #13#10;
  Bat := Bat + 'rem SimPit ortho scenery mount - launched at logon via HKCU Run.' + #13#10;
  Bat := Bat + 'rem Generated by the Simpit installer; edits are overwritten on upgrade.' + #13#10;
  Bat := Bat + 'rem Cache max-age must stay effectively infinite: 0 would purge' + #13#10;
  Bat := Bat + 'rem primed scenery within a minute. Size cap is the eviction mechanism.' + #13#10;
  Bat := Bat + 'title SimPit Ortho Mount (' + Drive + ')' + #13#10;
  Bat := Bat + 'rem Perf flags: ortho DSFs open thousands of tiny .ter files; long' + #13#10;
  Bat := Bat + 'rem dir/attr cache + fast-fingerprint avoid an SMB round trip per open.' + #13#10;
  Bat := Bat + 'rem Cleaner poll stays SHORT (2m): longer intervals let the cache balloon' + #13#10;
  Bat := Bat + 'rem past the cap, then a giant eviction burst deletes textures X-Plane' + #13#10;
  Bat := Bat + 'rem still has memory-mapped -> EXCEPTION_IN_PAGE_ERROR crash.' + #13#10;
  Bat := Bat + '"' + RclonePath + '" mount "' + Remote + '" ' + Drive + ' ^' + #13#10;
  Bat := Bat + '  --cache-dir "' + CacheDir + '" ^' + #13#10;
  Bat := Bat + '  --vfs-cache-mode full ^' + #13#10;
  Bat := Bat + '  --vfs-cache-max-size ' + CacheGB + 'G ^' + #13#10;
  Bat := Bat + '  --vfs-cache-max-age 8760h ^' + #13#10;
  Bat := Bat + '  --vfs-cache-poll-interval 2m ^' + #13#10;
  Bat := Bat + '  --vfs-fast-fingerprint ^' + #13#10;
  Bat := Bat + '  --dir-cache-time 12h ^' + #13#10;
  Bat := Bat + '  --attr-timeout 60s ^' + #13#10;
  Bat := Bat + '  --log-file "' + ExpandConstant('{app}') + '\ortho_mount.log" --log-level INFO ^' + #13#10;
  Bat := Bat + '  --rc --rc-addr 127.0.0.1:5572 --rc-no-auth' + #13#10;
  Bat := Bat + 'echo.' + #13#10;
  Bat := Bat + 'echo Mount exited (code %ERRORLEVEL%). Window stays open so the error is readable.' + #13#10;
  Bat := Bat + 'pause' + #13#10;
  SaveStringToFile(ExpandConstant('{app}\ortho_mount.bat'), Bat, False);

  // A previous install may have cached into a different folder. That
  // cache is just re-downloadable scenery, but it can be huge - offer
  // to reclaim the space rather than orphaning it silently.
  if RegQueryStringValue(HKCU, 'Software\Simpit', 'OrthoCacheDir', PriorCache) and
     (PriorCache <> '') and (CompareText(PriorCache, CacheDir) <> 0) and
     DirExists(PriorCache) then
    if MsgBox(
        'The previous install kept its scenery cache in:' + #13#10 +
        '  ' + PriorCache + #13#10 + #13#10 +
        'The new cache folder is:' + #13#10 +
        '  ' + CacheDir + #13#10 + #13#10 +
        'Delete the old cache folder to free its disk space?',
        mbConfirmation, MB_YESNO) = IDYES then
      DelTree(PriorCache, True, True, True);

  // Breadcrumbs: the next installer run prefills its pages from these
  // instead of asking for everything from scratch.
  RegWriteStringValue(HKCU, 'Software\Simpit', 'OrthoHost',     Trim(OrthoPage.Values[0]));
  RegWriteStringValue(HKCU, 'Software\Simpit', 'OrthoShare',    Trim(OrthoPage.Values[1]));
  RegWriteStringValue(HKCU, 'Software\Simpit', 'OrthoUser',     Trim(OrthoPage.Values[2]));
  RegWriteStringValue(HKCU, 'Software\Simpit', 'OrthoDrive',    UpperCase(Trim(OrthoMountPage.Values[0])));
  RegWriteStringValue(HKCU, 'Software\Simpit', 'OrthoCacheGB',  CacheGB);
  RegWriteStringValue(HKCU, 'Software\Simpit', 'OrthoCacheDir', CacheDir);
end;

procedure GenerateLaunchXPlaneBat;
// Gated X-Plane launcher. The rclone process starting is NOT the
// mount-ready signal: with a large VFS cache it reconciles for minutes
// before WinFsp attaches the drive letter, and X-Plane launched into
// that gap loads with no ortho scenery at all (the Custom Scenery
// symlink target doesn't exist yet). The drive letter only appears
// once the mount is served, so a readable scenery_packs.ini through
// the mount drive is the ready gate.
var
  Drive, XPFolder, XPExe, Bat: String;
begin
  Drive    := UpperCase(Trim(OrthoMountPage.Values[0])) + ':';
  XPFolder := RemoveBackslashUnlessRoot(Trim(XPlanePage.Values[0]));
  XPExe    := Trim(XPlanePage.Values[1]);
  if XPExe = '' then XPExe := 'X-Plane.exe';

  Bat := '@echo off' + #13#10;
  Bat := Bat + 'rem SimPit X-Plane launcher - gates launch on the ortho mount being ready.' + #13#10;
  Bat := Bat + 'rem Generated by the Simpit installer; edits are overwritten on upgrade.' + #13#10;
  Bat := Bat + 'title SimPit X-Plane Launcher' + #13#10;
  Bat := Bat + 'setlocal' + #13#10;
  Bat := Bat + 'set "READY_FILE=' + Drive + '\scenery_packs.ini"' + #13#10;
  Bat := Bat + 'set "XPLANE_EXE=' + XPFolder + '\' + XPExe + '"' + #13#10;
  Bat := Bat + 'set /a WAITED=0' + #13#10;
  Bat := Bat + 'if exist "%READY_FILE%" goto ready' + #13#10;
  Bat := Bat + 'echo Waiting for ortho mount (' + Drive + ') to come up...' + #13#10;
  Bat := Bat + 'echo (rclone reconciles its cache before attaching the drive; a few minutes is normal)' + #13#10;
  Bat := Bat + ':wait' + #13#10;
  Bat := Bat + 'if exist "%READY_FILE%" goto ready' + #13#10;
  Bat := Bat + 'timeout /t 5 /nobreak >nul' + #13#10;
  Bat := Bat + 'set /a WAITED+=5' + #13#10;
  Bat := Bat + 'echo   still waiting... %WAITED%s' + #13#10;
  Bat := Bat + 'if %WAITED% geq 600 goto timeout' + #13#10;
  Bat := Bat + 'goto wait' + #13#10;
  Bat := Bat + ':ready' + #13#10;
  Bat := Bat + 'echo Ortho mount is ready. Launching X-Plane...' + #13#10;
  Bat := Bat + 'start "" "%XPLANE_EXE%"' + #13#10;
  Bat := Bat + 'exit /b 0' + #13#10;
  Bat := Bat + ':timeout' + #13#10;
  Bat := Bat + 'echo.' + #13#10;
  Bat := Bat + 'echo ERROR: mount not ready after 10 minutes.' + #13#10;
  Bat := Bat + 'echo Is the "SimPit Ortho Mount (' + Drive + ')" window running? (ortho_mount.bat)' + #13#10;
  Bat := Bat + 'pause' + #13#10;
  Bat := Bat + 'exit /b 1' + #13#10;
  SaveStringToFile(ExpandConstant('{app}\launch_xplane.bat'), Bat, False);
end;

function GetLinkTarget(const Path: String): String;
// Read where an existing junction/symlink points (PowerShell does the
// reparse-point parsing for us). Empty string when unreadable.
var
  TmpFile, PSCmd: String;
  RC: Integer;
  S: AnsiString;
begin
  Result := '';
  TmpFile := ExpandConstant('{tmp}\linktarget.txt');
  PSCmd := '-NoProfile -NonInteractive -Command "' +
           '(Get-Item ''' + Path + ''' -Force).Target ' +
           '| Out-File -Encoding ascii ''' + TmpFile + '''"';
  Exec('powershell.exe', PSCmd, '', SW_HIDE, ewWaitUntilTerminated, RC);
  if LoadStringFromFile(TmpFile, S) then
    Result := Trim(String(S));
  DeleteFile(TmpFile);
end;

procedure LinkCustomScenery;
// Redirect <X-Plane>\Custom Scenery to the mounted ortho drive so
// X-Plane loads scenery straight from the NAS cache. Whatever was
// there before is preserved for uninstall: a real folder is renamed to
// "Custom Scenery.pre-ortho"; an existing link (e.g. a direct UNC link
// to the NAS from before the cached-mount era) has its target recorded
// so the uninstaller can recreate it. Breadcrumbs live in HKCU.
var
  XPFolder, Scenery, Backup, Drive, PriorTarget: String;
  RenamedNow, LinkOk: Boolean;
  RC: Integer;
begin
  XPFolder := Trim(XPlanePage.Values[0]);
  if XPFolder = '' then
  begin
    MsgBox(
      'No X-Plane folder was entered, so Custom Scenery was not linked ' +
      'to the ortho drive.' + #13#10 +
      'X-Plane will not see the NAS scenery until you link it manually:' + #13#10 +
      '  mklink /J "<X-Plane>\Custom Scenery" "<drive>:\"',
      mbInformation, MB_OK);
    Exit;
  end;

  Drive   := UpperCase(Trim(OrthoMountPage.Values[0])) + ':\';
  Scenery := AddBackslash(XPFolder) + 'Custom Scenery';
  Backup  := Scenery + '.pre-ortho';
  RenamedNow  := False;
  PriorTarget := '';

  if DirExists(Scenery) then
  begin
    if IsReparsePoint(Scenery) then
    begin
      // An existing link (prior install of ours, or a manual UNC link
      // straight to the NAS). Removing it deletes only the reparse
      // point - but capture its target first so uninstall can put the
      // machine back exactly as found. Ignore links that already point
      // at our own mount drive (nothing worth restoring).
      PriorTarget := GetLinkTarget(Scenery);
      if UpperCase(Copy(PriorTarget, 1, 2)) = UpperCase(Copy(Drive, 1, 2)) then
        PriorTarget := '';
      RemoveDir(Scenery);
    end
    else if not DirExists(Backup) then
    begin
      if not RenameFile(Scenery, Backup) then
      begin
        MsgBox(
          'Could not rename the existing Custom Scenery folder (is ' +
          'X-Plane running?). The ortho drive was NOT linked.' + #13#10 +
          'Close X-Plane and re-run this installer to finish the setup.',
          mbError, MB_OK);
        Exit;
      end;
      RenamedNow := True;
    end
    else
    begin
      // A real folder AND a backup both exist - a previous setup was
      // interrupted. Refuse to guess which one the user wants to keep.
      MsgBox(
        'Both "Custom Scenery" and "Custom Scenery.pre-ortho" exist in ' +
        'the X-Plane folder. Resolve this manually (keep one), then ' +
        're-run this installer. The ortho drive was NOT linked.',
        mbError, MB_OK);
      Exit;
    end;
  end;

  // Junction first: needs no elevation and WinFsp disk mounts qualify.
  // The target is written as "X:\." - a bare "X:\" inside quotes would
  // make cmd treat \" as an escaped quote and mangle the argument;
  // mklink normalizes the trailing dot back to the drive root.
  Exec('cmd.exe', '/c mklink /J "' + Scenery + '" "' + Drive + '."',
       '', SW_HIDE, ewWaitUntilTerminated, RC);
  LinkOk := IsReparsePoint(Scenery);
  if not LinkOk then
  begin
    // Some volume types refuse junctions - fall back to a directory
    // symlink, which needs elevation (same UAC pattern as the
    // firewall rule).
    ShellExec('runas', 'cmd.exe',
              '/c mklink /D "' + Scenery + '" "' + Drive + '."',
              '', SW_HIDE, ewWaitUntilTerminated, RC);
    LinkOk := IsReparsePoint(Scenery);
  end;

  if LinkOk then
  begin
    RegWriteStringValue(HKCU, 'Software\Simpit', 'OrthoSceneryLink', Scenery);
    if DirExists(Backup) then
      RegWriteStringValue(HKCU, 'Software\Simpit', 'OrthoSceneryBackup', Backup)
    else
      RegWriteStringValue(HKCU, 'Software\Simpit', 'OrthoSceneryBackup', '');
    RegWriteStringValue(HKCU, 'Software\Simpit', 'OrthoSceneryPriorLink',
                        PriorTarget);
  end
  else
  begin
    // Undo the rename we just did so the machine is exactly as before.
    if RenamedNow and (not DirExists(Scenery)) and DirExists(Backup) then
      RenameFile(Backup, Scenery);
    MsgBox(
      'Could not create the Custom Scenery link (junction and symlink ' +
      'both failed). The original folder has been restored.' + #13#10 +
      'You can create the link manually later:' + #13#10 +
      '  mklink /J "' + Scenery + '" "' + Drive + '"',
      mbError, MB_OK);
  end;
end;

procedure StartOrthoMountNow;
// Launch the mount immediately (minimized console - visible in the
// taskbar, not hidden) and confirm the drive letter appears.
var
  Drive: String;
  RC, I: Integer;
begin
  ShellExec('open', ExpandConstant('{app}\ortho_mount.bat'), '', '',
            SW_SHOWMINIMIZED, ewNoWait, RC);
  Drive := UpperCase(Trim(OrthoMountPage.Values[0])) + ':\';
  for I := 1 to 20 do
  begin
    if DirExists(Drive) then Break;
    Sleep(1000);
  end;
  if not DirExists(Drive) then
    MsgBox(
      'The ortho scenery mount did not come up within 20 seconds.' + #13#10 + #13#10 +
      'Check the "SimPit Ortho Mount" console window in the taskbar ' +
      'for the error (wrong NAS password is the most common cause).' + #13#10 +
      'It will retry automatically at the next logon.',
      mbError, MB_OK);
end;

function JsonStr(const Json, Key: String): String;
// Minimal extractor for the flat "key": "value" pairs this installer's
// own EscapeJson writes (only \\ and \" are escaped). Empty when the
// key is absent. Not a general JSON parser - do not point it at
// arbitrary files.
var
  P, E: Integer;
begin
  Result := '';
  P := Pos('"' + Key + '": "', Json);
  if P = 0 then Exit;
  P := P + Length(Key) + 6;
  E := P;
  while E <= Length(Json) do
  begin
    if Json[E] = '\' then
    begin
      Result := Result + Json[E + 1];
      E := E + 2;
    end
    else if Json[E] = '"' then
      Break
    else
    begin
      Result := Result + Json[E];
      E := E + 1;
    end;
  end;
end;

procedure PrefillFromPriorInstall;
// A re-run should never ask for what a previous install already knows:
// the security key comes back from simpit.key, and identity / X-Plane /
// backup settings from slave-config.json. Every field stays editable -
// these are defaults, not locks.
var
  Cfg: AnsiString;
  Json, S: String;
begin
  if LoadStringFromFile(KeyFilePath, Cfg) then
    if Trim(String(Cfg)) <> '' then
      KeyPage.Values[0] := Trim(String(Cfg));

  if not LoadStringFromFile(
      ExpandConstant('{userappdata}\simpit-slave\slave-config.json'), Cfg) then
    Exit;
  Json := String(Cfg);
  S := JsonStr(Json, 'name');          if S <> '' then IdentityPage.Values[0] := S;
  S := JsonStr(Json, 'control_host');  if S <> '' then IdentityPage.Values[1] := S;
  S := JsonStr(Json, 'XPLANE_FOLDER'); if S <> '' then XPlanePage.Values[0]  := S;
  S := JsonStr(Json, 'XPLANE_EXE');    if S <> '' then XPlanePage.Values[1]  := S;
  S := JsonStr(Json, 'BACKUP_FOLDER');
  if S <> '' then
  begin
    BackupPage.Values[0] := S;
    BackupEnableCheck.Checked := True;
    BackupEnableToggle(BackupEnableCheck);
  end;
  S := JsonStr(Json, 'BACKUP_KEEP');   if S <> '' then BackupPage.Values[1] := S;
end;

procedure PrefillOrthoFromPrior;
// Ortho pages: prefill from the HKCU breadcrumbs SetupOrthoMount now
// writes. Installs that predate the breadcrumbs get drive letter,
// share, cache size and cache folder recovered from the mount bat the
// previous install generated. If an rclone remote is already saved,
// say so on the password field instead of silently expecting the user
// to know that blank means "keep".
var
  S, Bat, Remote: String;
  Cfg: AnsiString;
  P, E: Integer;
begin
  if RegQueryStringValue(HKCU, 'Software\Simpit', 'OrthoHost', S) and (S <> '') then
    OrthoPage.Values[0] := S;
  if RegQueryStringValue(HKCU, 'Software\Simpit', 'OrthoShare', S) and (S <> '') then
    OrthoPage.Values[1] := S;
  if RegQueryStringValue(HKCU, 'Software\Simpit', 'OrthoUser', S) and (S <> '') then
    OrthoPage.Values[2] := S;
  if RegQueryStringValue(HKCU, 'Software\Simpit', 'OrthoDrive', S) and (S <> '') then
    OrthoMountPage.Values[0] := S;
  if RegQueryStringValue(HKCU, 'Software\Simpit', 'OrthoCacheGB', S) and (S <> '') then
    OrthoMountPage.Values[1] := S;
  if RegQueryStringValue(HKCU, 'Software\Simpit', 'OrthoCacheDir', S) and (S <> '') then
    OrthoMountPage.Values[3] := S;

  if (not RegQueryStringValue(HKCU, 'Software\Simpit', 'OrthoCacheGB', S)) or (S = '') then
    if LoadStringFromFile(AddBackslash(WizardDirValue) + 'ortho_mount.bat', Cfg) then
    begin
      Bat := String(Cfg);
      P := Pos(' mount "', Bat);
      if P > 0 then
      begin
        P := P + 8;
        E := P;
        while (E <= Length(Bat)) and (Bat[E] <> '"') do E := E + 1;
        Remote := Copy(Bat, P, E - P);      // e.g. randhawanas:XPlane12/Custom Scenery
        P := Pos(':', Remote);
        if (P > 0) and (P < Length(Remote)) then
          OrthoPage.Values[1] := Copy(Remote, P + 1, Length(Remote));
        // Drive letter follows the closing quote and a space.
        if E + 2 <= Length(Bat) then
          OrthoMountPage.Values[0] := Copy(Bat, E + 2, 1);
      end;
      P := Pos('--vfs-cache-max-size ', Bat);
      if P > 0 then
      begin
        P := P + 21;
        S := '';
        while (P <= Length(Bat)) and (Bat[P] >= '0') and (Bat[P] <= '9') do
        begin
          S := S + Bat[P];
          P := P + 1;
        end;
        if S <> '' then OrthoMountPage.Values[1] := S;
      end;
      P := Pos('--cache-dir "', Bat);
      if P > 0 then
      begin
        P := P + 13;
        E := P;
        while (E <= Length(Bat)) and (Bat[E] <> '"') do E := E + 1;
        if E > P then OrthoMountPage.Values[3] := Copy(Bat, P, E - P);
      end;
    end;

  if LoadStringFromFile(ExpandConstant('{userappdata}\rclone\rclone.conf'), Cfg) then
    if Pos('[randhawanas]', String(Cfg)) > 0 then
      OrthoPage.PromptLabels[3].Caption :=
        'NAS password (leave blank to keep the saved one):';
end;

procedure InitializeWizard;
var
  BrowseBtn: TButton;
begin
  // Page 1: Security key
  KeyPage := CreateInputQueryPage(
    wpSelectComponents,
    'Simpit Security Key',
    'Enter the shared key from Simpit Control',
    'On the master machine, open Simpit Control and click the Security button.' + #13#10 +
    'The key and this PC''s IP address are both shown there.' + #13#10 + #13#10 +
    'Paste the 64-character hex key below, or click Browse to load a simpit.key file.' + #13#10 +
    '(In Simpit Control: Security -> Save to file... to export the key file.)');
  KeyPage.Add('Security key (64 hex characters):', False);
  BrowseBtn := TButton.Create(KeyPage);
  BrowseBtn.Parent := KeyPage.Surface;
  BrowseBtn.Caption := 'Browse for simpit.key...';
  BrowseBtn.Width := ScaleX(160);
  BrowseBtn.Height := ScaleY(23);
  BrowseBtn.Top := KeyPage.Edits[0].Top + KeyPage.Edits[0].Height + ScaleY(8);
  BrowseBtn.Left := 0;
  BrowseBtn.OnClick := @BrowseForKeyFile;

  // Page 2: Slave identity
  IdentityPage := CreateInputQueryPage(
    KeyPage.ID,
    'Slave Identity',
    'Name this machine and enter the Control IP',
    'The slave name is shown in Simpit Control''s slave list.' + #13#10 +
    'The Control IP is displayed in Simpit Control''s title bar.');
  IdentityPage.Add('Slave display name:', False);
  IdentityPage.Add('Simpit Control IP address:', False);
  IdentityPage.Values[0] := GetComputerNameString;
  // Default to this machine's own IP: it puts the right subnet in the
  // field so the user usually only edits the last octet. Detection
  // failure leaves the field blank; either way it stays editable and
  // NextButtonClick still validates whatever is entered.
  IdentityPage.Values[1] := GetLocalIPAddress;

  // Page 3: X-Plane configuration
  XPlanePage := CreateInputQueryPage(
    IdentityPage.ID,
    'X-Plane Configuration',
    'Enter the X-Plane install details for this machine',
    'These values are sent to Simpit Control and used when running scripts.' + #13#10 +
    'Leave blank if X-Plane is not installed on this machine.');
  XPlanePage.Add('X-Plane folder (e.g. C:\X-Plane 12):', False);
  XPlanePage.Add('X-Plane executable (default: X-Plane.exe):', False);
  XPlanePage.Values[1] := 'X-Plane.exe';
  BrowseBtn := TButton.Create(XPlanePage);
  BrowseBtn.Parent := XPlanePage.Surface;
  BrowseBtn.Caption := 'Browse...';
  BrowseBtn.Width := ScaleX(90);
  BrowseBtn.Height := ScaleY(23);
  // Shrink the folder edit so the button fits to its right without overlap.
  XPlanePage.Edits[0].Width := XPlanePage.Edits[0].Width - ScaleX(98);
  BrowseBtn.Left := XPlanePage.Edits[0].Left + XPlanePage.Edits[0].Width + ScaleX(8);
  BrowseBtn.Top  := XPlanePage.Edits[0].Top +
                    (XPlanePage.Edits[0].Height - BrowseBtn.Height) div 2;
  BrowseBtn.OnClick := @BrowseForXPlaneFolder;

  // Page 4: Backup configuration (optional, explicit opt-in checkbox)
  BackupPage := CreateInputQueryPage(
    XPlanePage.ID,
    'Backup Configuration (Optional)',
    'Configure X-Plane backup settings',
    'Tick the box below to set up backups on this machine, or leave it ' +
    'unticked to skip. You can re-run this installer later to add them.');
  BackupPage.Add('Backup folder (e.g. D:\XPlane-Backup):', False);
  BackupPage.Add('Number of backups to keep (default: 5):', False);
  BackupPage.Values[1] := '5';
  BackupBrowseBtn := TButton.Create(BackupPage);
  BackupBrowseBtn.Parent := BackupPage.Surface;
  BackupBrowseBtn.Caption := 'Browse...';
  BackupBrowseBtn.Width := ScaleX(90);
  BackupBrowseBtn.Height := ScaleY(23);
  // Shrink the folder edit so the button fits to its right without overlap.
  BackupPage.Edits[0].Width := BackupPage.Edits[0].Width - ScaleX(98);
  BackupBrowseBtn.Left := BackupPage.Edits[0].Left + BackupPage.Edits[0].Width + ScaleX(8);
  BackupBrowseBtn.Top  := BackupPage.Edits[0].Top +
                    (BackupPage.Edits[0].Height - BackupBrowseBtn.Height) div 2;
  BackupBrowseBtn.OnClick := @BrowseForBackupFolder;
  BackupEnableCheck := TNewCheckBox.Create(BackupPage);
  BackupEnableCheck.Parent := BackupPage.Surface;
  BackupEnableCheck.Caption := 'Set up X-Plane backups on this machine';
  BackupEnableCheck.Top := BackupPage.Edits[1].Top + BackupPage.Edits[1].Height + ScaleY(12);
  BackupEnableCheck.Left := 0;
  BackupEnableCheck.Width := BackupPage.SurfaceWidth;
  BackupEnableCheck.Checked := False;
  BackupEnableCheck.OnClick := @BackupEnableToggle;
  BackupEnableToggle(BackupEnableCheck);

  // Page 5: Ortho scenery cache opt-in
  OrthoOptPage := CreateInputOptionPage(
    BackupPage.ID,
    'Ortho Scenery Cache (Optional)',
    'Stream shared ortho scenery from the NAS',
    'Simpit can mount the shared ortho scenery library from the NAS as a ' +
    'local drive with a disk cache, so X-Plane streams scenery without ' +
    'needing a full local copy.' + #13#10 + #13#10 +
    'Requires rclone and WinFsp - if they are missing, the installer ' +
    'offers to fetch them with winget.' + #13#10 + #13#10 +
    'X-Plane''s Custom Scenery folder will be redirected (linked) to the ' +
    'mounted drive; the original folder is kept and restored on uninstall.' + #13#10 + #13#10 +
    'Untick to skip. You can re-run this installer later to add it.',
    False, False);
  OrthoOptPage.Add('Set up the ortho scenery cache mount on this machine');
  OrthoOptPage.Values[0] := True;

  // Page 6: Ortho settings (skipped when the box above is unticked)
  OrthoPage := CreateInputQueryPage(
    OrthoOptPage.ID,
    'Ortho Cache Settings',
    'NAS connection',
    'Defaults match the standard Simpit fleet. The NAS password is stored ' +
    '(obscured) only in this machine''s rclone configuration. Leave the ' +
    'password blank to reuse an existing rclone configuration.');
  OrthoPage.Add('NAS host name or IP:', False);
  OrthoPage.Add('Share and folder (share/path):', False);
  OrthoPage.Add('NAS username:', False);
  OrthoPage.Add('NAS password:', True);
  OrthoPage.Values[0] := 'RandhawaNAS';
  OrthoPage.Values[1] := 'XPlane12/Custom Scenery';
  OrthoPage.Values[2] := 'mysands';
  OrthoPage.Values[3] := '';

  // Page 7: Ortho mount settings. Separate page: a single
  // InputQueryPage can only show ~5 fields before the rest fall below
  // the visible surface (the cache-size field was invisible with 7).
  OrthoMountPage := CreateInputQueryPage(
    OrthoPage.ID,
    'Ortho Cache Mount Settings',
    'Local drive, cache size and rclone location',
    'The scenery share is mounted as a local drive with a disk cache. ' +
    'Cache size guidance: one full-detail (Z18) tile is roughly 28 GB; ' +
    '160 GB covers a metro-area hybrid profile with headroom.');
  OrthoMountPage.Add('Mount drive letter (single letter):', False);
  OrthoMountPage.Add('Cache size (GB):', False);
  OrthoMountPage.Add('rclone.exe location (auto-detected; Browse if wrong):', False);
  OrthoMountPage.Add('Cache folder (put it on a drive with enough free space):', False);
  OrthoMountPage.Values[0] := 'X';
  // 160 GB default: 120 GB was still measured thrashing (continuous
  // evict+refetch pinned at cap) once flights left the WUS/ZLA Z16
  // bbox, where hybrid falls back to full-nationwide Z18 (see
  // set_scenery_profile.py coverage note). 50 GB thrashed even worse.
  OrthoMountPage.Values[1] := '160';
  OrthoMountPage.Values[2] := FindRclone;
  BrowseBtn := TButton.Create(OrthoMountPage);
  BrowseBtn.Parent := OrthoMountPage.Surface;
  BrowseBtn.Caption := 'Browse...';
  BrowseBtn.Width := ScaleX(90);
  BrowseBtn.Height := ScaleY(23);
  OrthoMountPage.Edits[2].Width := OrthoMountPage.Edits[2].Width - ScaleX(98);
  BrowseBtn.Left := OrthoMountPage.Edits[2].Left + OrthoMountPage.Edits[2].Width + ScaleX(8);
  BrowseBtn.Top  := OrthoMountPage.Edits[2].Top +
                    (OrthoMountPage.Edits[2].Height - BrowseBtn.Height) div 2;
  BrowseBtn.OnClick := @BrowseForRclone;
  // Cache folder: default is rclone's own default location so existing
  // machines keep their warm cache after an upgrade.
  OrthoMountPage.Values[3] := ExpandConstant('{localappdata}\rclone');
  BrowseBtn := TButton.Create(OrthoMountPage);
  BrowseBtn.Parent := OrthoMountPage.Surface;
  BrowseBtn.Caption := 'Browse...';
  BrowseBtn.Width := ScaleX(90);
  BrowseBtn.Height := ScaleY(23);
  OrthoMountPage.Edits[3].Width := OrthoMountPage.Edits[3].Width - ScaleX(98);
  BrowseBtn.Left := OrthoMountPage.Edits[3].Left + OrthoMountPage.Edits[3].Width + ScaleX(8);
  BrowseBtn.Top  := OrthoMountPage.Edits[3].Top +
                    (OrthoMountPage.Edits[3].Height - BrowseBtn.Height) div 2;
  BrowseBtn.OnClick := @BrowseForCacheDir;

  // Re-runs default every page to what the previous install chose.
  PrefillFromPriorInstall;
  PrefillOrthoFromPrior;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  if not IsSlaveInstall then
    Result := (PageID = KeyPage.ID) or (PageID = IdentityPage.ID) or
              (PageID = XPlanePage.ID) or (PageID = BackupPage.ID) or
              (PageID = OrthoOptPage.ID) or (PageID = OrthoPage.ID) or
              (PageID = OrthoMountPage.ID)
  else
    // Ortho settings only matter when the user opted in earlier.
    Result := ((PageID = OrthoPage.ID) or (PageID = OrthoMountPage.ID))
              and not OrthoOptPage.Values[0];
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Key: String;
  IP: String;
  i: Integer;
  c: Char;
  valid: Boolean;
  CacheDir, CacheDrive: String;
  FreeB, TotalB: Int64;
begin
  Result := True;
  if not IsSlaveInstall then Exit;

  // Validate security key
  if CurPageID = KeyPage.ID then
  begin
    Key := Trim(KeyPage.Values[0]);
    valid := (Length(Key) = 64);
    if valid then
      for i := 1 to 64 do
      begin
        c := LowerCase(Key[i])[1];
        if not (((c >= '0') and (c <= '9')) or ((c >= 'a') and (c <= 'f'))) then
        begin
          valid := False;
          Break;
        end;
      end;
    if not valid then
    begin
      MsgBox(
        'Please enter a valid 64-character hex key.' + #13#10 +
        'Open Simpit Control, click the Security button, then click Copy.',
        mbError, MB_OK);
      Result := False;
    end;
  end;

  // Validate slave name and Control IP
  if CurPageID = IdentityPage.ID then
  begin
    if Trim(IdentityPage.Values[0]) = '' then
    begin
      MsgBox('Please enter a display name for this slave.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    IP := Trim(IdentityPage.Values[1]);
    if IP = '' then
    begin
      MsgBox(
        'Please enter the IP address of the Simpit Control machine.' + #13#10 +
        'It is shown in the title bar of Simpit Control.',
        mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if not IsValidIP(IP) then
    begin
      MsgBox(
        '"' + IP + '" is not a valid IP address.' + #13#10 +
        'Enter four numbers separated by dots, e.g. 192.168.1.100.' + #13#10 + #13#10 +
        'The IP is shown in the Simpit Control title bar.',
        mbError, MB_OK);
      Result := False;
    end;
  end;

  // Leaving the ortho opt-in page with the box ticked: check (and offer
  // to install) rclone + WinFsp. Never blocks - it unticks on failure.
  if CurPageID = OrthoOptPage.ID then
  begin
    if OrthoOptPage.Values[0] then
      EnsureOrthoPrereqs;
  end;

  // Validate ortho settings.
  if CurPageID = OrthoPage.ID then
  begin
    if (Trim(OrthoPage.Values[0]) = '') or (Trim(OrthoPage.Values[1]) = '') or
       (Trim(OrthoPage.Values[2]) = '') then
    begin
      MsgBox('NAS host, share/folder and username must all be filled in.',
             mbError, MB_OK);
      Result := False;
    end;
  end;

  // Validate ortho mount settings.
  if CurPageID = OrthoMountPage.ID then
  begin
    IP := UpperCase(Trim(OrthoMountPage.Values[0]));  // reuse IP as scratch
    if (Length(IP) <> 1) or (IP[1] < 'D') or (IP[1] > 'Z') then
    begin
      MsgBox('Mount drive letter must be a single letter D-Z (not a ' +
             'drive that is already in use).', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    i := StrToIntDef(Trim(OrthoMountPage.Values[1]), -1);
    if (i < 1) or (i > 2000) then
    begin
      MsgBox('Cache size must be a whole number of GB between 1 and 2000.',
             mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if not FileExists(Trim(OrthoMountPage.Values[2])) then
    begin
      MsgBox('rclone.exe was not found at the given location.' + #13#10 +
             'Click Browse and select rclone.exe (winget installs it under' + #13#10 +
             '<profile>\AppData\Local\Microsoft\WinGet\Links, or' + #13#10 +
             'C:\Program Files\WinGet\Links for machine-wide installs).',
             mbError, MB_OK);
      Result := False;
      Exit;
    end;
    // Cache folder: the drive must exist and should have room for the
    // whole cache (plus slack for overshoot between cleaner passes).
    CacheDir := Trim(OrthoMountPage.Values[3]);
    if CacheDir = '' then
    begin
      MsgBox('Please enter a cache folder (or keep the default).',
             mbError, MB_OK);
      Result := False;
      Exit;
    end;
    CacheDrive := ExtractFileDrive(CacheDir);
    if (CacheDrive = '') or (not DirExists(CacheDrive + '\')) then
    begin
      MsgBox('The cache folder must be on an existing local drive ' +
             '(e.g. D:\rclone-cache). Drive "' + CacheDrive + '" was not found.',
             mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if GetSpaceOnDisk64(CacheDrive + '\', FreeB, TotalB) then
    begin
      // i still holds the validated cache size in GB from above.
      if FreeB < Int64(i) * 1073741824 then
        if MsgBox('Drive ' + CacheDrive + ' has ' +
               IntToStr(FreeB div 1073741824) + ' GB free, but the cache ' +
               'is set to ' + IntToStr(i) + ' GB. The cache will not fit ' +
               'and X-Plane may crash when the disk fills.' + #13#10 + #13#10 +
               'Continue anyway?', mbConfirmation, MB_YESNO) = IDNO then
        begin
          Result := False;
          Exit;
        end;
    end;
  end;
end;

procedure AddSlaveFirewallRules;
// Add a single Windows Firewall inbound rule that allows all traffic
// to/from simpit-slave.exe.  Program-based rules are simpler than
// port-based rules: one rule covers both UDP and TCP automatically,
// and Windows links it to the executable rather than a port number that
// could change.
//
// -Profile Any ensures the rule applies on all network types (Domain,
// Private, Public) — important on Windows 11 which often classifies new
// interfaces as Public.  -ExecutionPolicy Bypass handles systems where
// the PowerShell execution policy is Restricted.
//
// If the user cancels UAC or the command fails we open Windows Firewall
// and walk them through the simpler "Allow an app" screen instead.
var
  AppExe: String;
  PSCmd: String;
  ResultCode: Integer;
  Succeeded: Boolean;
begin
  AppExe := ExpandConstant('{app}\simpit-slave.exe');
  MsgBox(
    'Simpit Slave needs to receive connections from Simpit Control.' + #13#10 + #13#10 +
    'Windows will ask for administrator permission to add a firewall rule.' + #13#10 +
    'Please click Yes on the next prompt.',
    mbInformation, MB_OK);
  // Single program-based inbound rule — covers all protocols at once.
  PSCmd :=
    '-ExecutionPolicy Bypass -WindowStyle Hidden -NonInteractive -Command "' +
    'New-NetFirewallRule -DisplayName ''Simpit Slave'' ' +
    '-Direction Inbound -Action Allow ' +
    '-Program ''' + AppExe + ''' ' +
    '-Profile Any -Enabled True -ErrorAction Stop"';
  Succeeded := ShellExec('runas', 'powershell.exe', PSCmd, '',
                         SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if (not Succeeded) or (ResultCode <> 0) then
  begin
    MsgBox(
      'The automatic firewall rule could not be added.' + #13#10 + #13#10 +
      'This usually happens when you click No on the permission prompt,' + #13#10 +
      'or when your organisation''s security policy blocks the command.' + #13#10 + #13#10 +
      'Click OK and Windows Firewall will open automatically.' + #13#10 +
      'Follow the on-screen instructions to allow Simpit Slave.',
      mbError, MB_OK);
    ShowFirewallAllowApp;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Key: String;
  DataDir: String;
  SlaveName: String;
  ControlIP: String;
  XPlaneFolder: String;
  XPlaneExe: String;
  BackupFolder: String;
  BackupKeep: String;
  Json: String;
  ResultCode: Integer;
begin
  if IsSlaveInstall then
  begin
    // ssPostInstall: write key file and slave-config.json.
    if CurStep = ssPostInstall then
    begin
      Key        := LowerCase(Trim(KeyPage.Values[0]));
      DataDir    := ExpandConstant('{userappdata}\simpit-slave');
      SlaveName  := Trim(IdentityPage.Values[0]);
      ControlIP  := Trim(IdentityPage.Values[1]);
      XPlaneFolder := Trim(XPlanePage.Values[0]);
      XPlaneExe    := Trim(XPlanePage.Values[1]);
      // Backups are an explicit opt-in: an unticked checkbox means no
      // backup env vars land in slave-config.json even if the fields
      // hold leftover text.
      if BackupEnableCheck.Checked then
        BackupFolder := Trim(BackupPage.Values[0])
      else
        BackupFolder := '';
      BackupKeep   := Trim(BackupPage.Values[1]);

      if not DirExists(DataDir) then
        CreateDir(DataDir);

      // Write simpit.key
      SaveStringToFile(KeyFilePath, Key + #10, False);

      // Build and write slave-config.json
      if SlaveName = '' then
        SlaveName := GetComputerNameString;
      if XPlaneExe = '' then
        XPlaneExe := 'X-Plane.exe';
      if BackupKeep = '' then
        BackupKeep := '5';

      Json := '{' + #10;
      Json := Json + '  "name": "' + EscapeJson(SlaveName) + '",' + #10;
      Json := Json + '  "control_host": "' + EscapeJson(ControlIP) + '",' + #10;
      Json := Json + '  "udp_port": 49100,' + #10;
      Json := Json + '  "tcp_port": 49101,' + #10;
      Json := Json + '  "env": {' + #10;
      Json := Json + '    "XPLANE_FOLDER": "' + EscapeJson(XPlaneFolder) + '",' + #10;
      Json := Json + '    "XPLANE_EXE": "' + EscapeJson(XPlaneExe) + '"';
      if BackupFolder <> '' then
      begin
        Json := Json + ',' + #10;
        Json := Json + '    "BACKUP_FOLDER": "' + EscapeJson(BackupFolder) + '",' + #10;
        Json := Json + '    "BACKUP_KEEP": "' + EscapeJson(BackupKeep) + '"';
      end;
      Json := Json + #10 + '  }' + #10 + '}' + #10;

      SaveStringToFile(DataDir + '\slave-config.json', Json, False);

      // Add Windows Firewall inbound rules so the slave can bind its ports.
      // Done here (after key + config are written, before ssDone launch)
      // so the agent is firewalled before it starts listening.
      AddSlaveFirewallRules;

      // Ortho scenery cache: stop any prior mount helper, write the
      // rclone remote and generate ortho_mount.bat (Run key added by
      // the [Registry] section, gated on the same OrthoSelected check),
      // then redirect X-Plane's Custom Scenery onto the mount drive.
      if OrthoSelected then
      begin
        SetupOrthoMount;
        GenerateLaunchXPlaneBat;
        LinkCustomScenery;
      end;
    end;

    // ssDone: launch slave silently then ask user to verify in Control.
    if CurStep = ssDone then
    begin
      if OrthoSelected then
        StartOrthoMountNow;

      Exec(ExpandConstant('{app}\simpit-slave.exe'), '', '', SW_HIDE, ewNoWait, ResultCode);

      // Give the slave time to start and receive a poll from Control.
      Sleep(4000);

      if MsgBox(
          'The Simpit Slave is now running on this machine.' + #13#10 + #13#10 +
          'On your master machine, open Simpit Control and check the Slaves list.' + #13#10 + #13#10 +
          'Does this machine appear as a slave?',
          mbConfirmation, MB_YESNO) = IDNO then
      begin
        TroubleshootSlave;
      end;
    end;
  end;
end;
