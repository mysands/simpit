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
Name: "{userdesktop}\Simpit Control"; Filename: "{app}\simpit-control.exe"; Components: control; Tasks: desktopicon

[Registry]
; Register slave to run at Windows startup (current user, no elevation needed)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "SimpitSlave"; \
    ValueData: """{app}\simpit-slave.exe"""; \
    Components: slave; Flags: uninsdeletevalue

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

function IsSlaveInstall: Boolean;
begin
  Result := WizardIsComponentSelected('slave');
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

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    // Kill the slave process before files are removed so Windows
    // does not complain that simpit-slave.exe is locked.
    Exec('taskkill.exe', '/F /IM simpit-slave.exe', '',
         SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(500);
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
  // Skip the matched '"key": "' literal: 1 quote + key + '": ' + 1
  // quote = Length(Key) + 5 (a +6 here once ate the first character
  // of every value - 'C:\X-Plane 12.1' became ':\X-Plane 12.1').
  P := P + Length(Key) + 5;
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
  S := JsonStr(Json, 'BACKUP_FOLDER'); if S <> '' then BackupPage.Values[0]  := S;
  S := JsonStr(Json, 'BACKUP_KEEP');   if S <> '' then BackupPage.Values[1]  := S;
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
  IdentityPage.Values[1] := '';

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

  // Page 4: Backup configuration (optional)
  BackupPage := CreateInputQueryPage(
    XPlanePage.ID,
    'Backup Configuration (Optional)',
    'Configure X-Plane backup settings',
    'Leave Backup folder blank to skip backup configuration.');
  BackupPage.Add('Backup folder (e.g. D:\XPlane-Backup):', False);
  BackupPage.Add('Number of backups to keep (default: 5):', False);
  BackupPage.Values[1] := '5';
  BrowseBtn := TButton.Create(BackupPage);
  BrowseBtn.Parent := BackupPage.Surface;
  BrowseBtn.Caption := 'Browse...';
  BrowseBtn.Width := ScaleX(90);
  BrowseBtn.Height := ScaleY(23);
  // Shrink the folder edit so the button fits to its right without overlap.
  BackupPage.Edits[0].Width := BackupPage.Edits[0].Width - ScaleX(98);
  BrowseBtn.Left := BackupPage.Edits[0].Left + BackupPage.Edits[0].Width + ScaleX(8);
  BrowseBtn.Top  := BackupPage.Edits[0].Top +
                    (BackupPage.Edits[0].Height - BrowseBtn.Height) div 2;
  BrowseBtn.OnClick := @BrowseForBackupFolder;

  // Re-runs default every page to what the previous install chose, so a
  // reinstall or upgrade never has to re-enter the key, Control IP,
  // X-Plane folder or backup settings by hand.
  PrefillFromPriorInstall;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := not IsSlaveInstall and
            ((PageID = KeyPage.ID) or (PageID = IdentityPage.ID) or
             (PageID = XPlanePage.ID) or (PageID = BackupPage.ID));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Key: String;
  IP: String;
  i: Integer;
  c: Char;
  valid: Boolean;
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

  // Validate the X-Plane folder: blank is allowed (no X-Plane on this
  // machine), but a non-blank folder must actually exist - a typo or a
  // stale prefill would otherwise flow silently into slave-config.json
  // and every script that reads XPLANE_FOLDER.
  if CurPageID = XPlanePage.ID then
  begin
    if (Trim(XPlanePage.Values[0]) <> '') and
       (not DirExists(Trim(XPlanePage.Values[0]))) then
    begin
      MsgBox(
        'The X-Plane folder does not exist:' + #13#10 +
        '  ' + Trim(XPlanePage.Values[0]) + #13#10 + #13#10 +
        'Fix the path (or leave it blank if X-Plane is not installed ' +
        'on this machine).',
        mbError, MB_OK);
      Result := False;
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

function ProcRunning(const ExeName: String): Boolean;
// tasklist always exits 0, so grep its output with find: RC 0 = the
// process name appeared, RC 1 = it did not.
var
  RC: Integer;
begin
  Result := Exec('cmd.exe',
    '/c tasklist /FI "IMAGENAME eq ' + ExeName + '" | find /I "' + ExeName + '"',
    '', SW_HIDE, ewWaitUntilTerminated, RC) and (RC = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
// Stop the running Simpit apps BEFORE the [Files] copy - overwriting a
// locked exe is what previously forced killing the slave by hand on
// every upgrade. taskkill /F returns before the process has actually
// exited, so poll until it is really gone (10 s cap; the copy then
// fails with Inno's normal retry dialog rather than silently).
var
  RC, I: Integer;
begin
  Result := '';
  Exec('taskkill.exe', '/F /IM simpit-slave.exe', '',
       SW_HIDE, ewWaitUntilTerminated, RC);
  Exec('taskkill.exe', '/F /IM simpit-control.exe', '',
       SW_HIDE, ewWaitUntilTerminated, RC);
  for I := 1 to 20 do
  begin
    if (not ProcRunning('simpit-slave.exe')) and
       (not ProcRunning('simpit-control.exe')) then
      Break;
    Sleep(500);
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
      BackupFolder := Trim(BackupPage.Values[0]);
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
    end;

    // ssDone: launch slave silently then ask user to verify in Control.
    if CurStep = ssDone then
    begin
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
