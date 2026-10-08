object LCSCImportForm: TLCSCImportForm
  Left = 0
  Top = 0
  ClientWidth = 540
  ClientHeight = 270
  Caption = 'LCSC component importer - AD 2024'
  BorderStyle = bsDialog
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
    Caption = 'LCSC code (symbol + footprint + embedded STEP)'
  end
  object CodeEdit: TEdit
    Left = 20
    Top = 46
    Width = 436
    Height = 25
    TabOrder = 0
    Text = 'C8734'
  end
  object StatusLabel: TLabel
    Left = 20
    Top = 85
    Width = 436
    Height = 60
    AutoSize = False
    WordWrap = True
    Caption = 'Enter an LCSC code and click Import.'
  end
  object StartButton: TButton
    Left = 246
    Top = 159
    Width = 100
    Height = 29
    Caption = 'Import'
    Default = True
    TabOrder = 1
    OnClick = StartButtonClick
  end
  object CancelButton: TButton
    Left = 356
    Top = 159
    Width = 100
    Height = 29
    Caption = 'Cancel'
    Cancel = True
    TabOrder = 2
    OnClick = CancelButtonClick
  end
  object PollTimer: TTimer
    Enabled = False
    Interval = 300
    OnTimer = PollTimerTimer
    Left = 32
    Top = 161
  end
end
