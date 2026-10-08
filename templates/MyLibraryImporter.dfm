object MyLibraryFormV31: TMyLibraryFormV31
  Left = 0
  Top = 0
  Width = 760
  Height = 440
  Caption = 'MyLibrary importer v0.3.1 - SCH + PCB Library'
  BorderStyle = bsSizeable
  BorderIcons = [biSystemMenu]
  Color = clBtnFace
  Font.Charset = DEFAULT_CHARSET
  Font.Color = clWindowText
  Font.Height = -13
  Font.Name = 'Segoe UI'
  Font.Style = []
  Position = poMainFormCenter
  Scaled = True
  Visible = False
  OnShow = FormShow
  PixelsPerInch = 96
  TextHeight = 17
  object CodeLabel: TLabel
    Left = 20
    Top = 20
    Width = 320
    Height = 17
    Caption = 'LCSC code -> MyParts + MyFootprints'
  end
  object CodeEdit: TEdit
    Left = 20
    Top = 46
    Width = 400
    Height = 25
    TabOrder = 0
    Text = 'C8734'
  end
  object StatusLabel: TLabel
    Left = 20
    Top = 85
    Width = 400
    Height = 60
    AutoSize = False
    WordWrap = True
    Caption = 'Enter an LCSC code and click Import.'
  end
  object StartButton: TButton
    Left = 20
    Top = 215
    Width = 110
    Height = 36
    Caption = 'Import'
    Default = True
    TabOrder = 1
    OnClick = StartButtonClick
  end
  object CancelButton: TButton
    Left = 150
    Top = 215
    Width = 110
    Height = 36
    Caption = 'Cancel'
    Cancel = True
    TabOrder = 2
    OnClick = CancelButtonClick
  end
  object CheckButton: TButton
    Left = 280
    Top = 215
    Width = 140
    Height = 36
    Caption = 'Check result'
    Enabled = False
    TabOrder = 3
    OnClick = CheckButtonClick
  end
  object PollTimer: TTimer
    Enabled = False
    Interval = 300
    OnTimer = PollTimerTimer
    Left = 32
    Top = 161
  end
  object ImportProgress: TProgressBar
    Left = 20
    Top = 165
    Width = 400
    Height = 22
    Min = 0
    Max = 100
    Position = 0
    TabOrder = 4
  end
end
