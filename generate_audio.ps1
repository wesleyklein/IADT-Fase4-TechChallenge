param(
    [Parameter(Mandatory)][string]$OutputPath,
    [Parameter(Mandatory)][string]$TranscriptPath,
    [switch]$Slow
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$voice = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $portuguese = $voice.GetInstalledVoices() | Where-Object {
        $_.Enabled -and $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq 'pt'
    } | Sort-Object { $_.VoiceInfo.Culture.Name -ne 'pt-BR' } | Select-Object -First 1
    if (-not $portuguese) {
        throw 'Instale uma voz de português compatível com SAPI no Windows.'
    }
    $voice.SelectVoice($portuguese.VoiceInfo.Name)
    if ($Slow) { $voice.Rate = -4 }
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
        16000, 'Sixteen', 'Mono'
    )
    $voice.SetOutputToWaveFile($OutputPath, $format)
    $voice.Speak([System.IO.File]::ReadAllText($TranscriptPath))
    $voice.Voice.Culture.Name
}
finally {
    $voice.Dispose()
}

