# EdgeScribe offline speech worker (Windows System.Speech; no network, no install).
# Protocol: one JSON request per stdin line -> one JSON response per stdout line.
#   {"id":1,"op":"voices"}
#   {"id":2,"op":"tts","text":"...","voice":"Microsoft Zira Desktop","rate":0,"out":"C:\\...\\x.wav"}
#   {"id":3,"op":"asr","path":"C:\\...\\in.wav","timeout":30}
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)

function Send($obj) { [Console]::Out.WriteLine(($obj | ConvertTo-Json -Compress -Depth 5)); [Console]::Out.Flush() }

Send @{ ready = $true }
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($line -eq $null) { break }
    if ($line.Trim() -eq '') { continue }
    $req = $null
    try {
        $req = $line | ConvertFrom-Json
        if ($req.op -eq 'voices') {
            $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
            $v = @($s.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object {
                @{ name = $_.VoiceInfo.Name; culture = $_.VoiceInfo.Culture.Name; gender = "$($_.VoiceInfo.Gender)" } })
            $s.Dispose()
            $rec = @([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | ForEach-Object { $_.Culture.Name })
            Send @{ id = $req.id; ok = $true; voices = $v; recognizers = $rec }
        }
        elseif ($req.op -eq 'tts') {
            $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
            try {
                if ($req.voice) { $s.SelectVoice([string]$req.voice) }
                $s.Rate = [Math]::Max(-10, [Math]::Min(10, [int]$req.rate))
                $s.SetOutputToWaveFile([string]$req.out, $fmt)
                $s.Speak([string]$req.text)
            } finally { $s.Dispose() }
            Send @{ id = $req.id; ok = $true }
        }
        elseif ($req.op -eq 'asr') {
            $tag = "r$($req.id)"
            $r = New-Object System.Speech.Recognition.SpeechRecognitionEngine([Globalization.CultureInfo]'en-US')
            try {
                $r.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
                $r.SetInputToWaveFile([string]$req.path)
                $null = Register-ObjectEvent $r SpeechRecognized -SourceIdentifier "$tag-rec"
                $null = Register-ObjectEvent $r RecognizeCompleted -SourceIdentifier "$tag-done"
                $r.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
                $done = Wait-Event -SourceIdentifier "$tag-done" -Timeout ([int]$req.wait)
                if (-not $done) { $r.RecognizeAsyncCancel() }
                $parts = @(Get-Event -SourceIdentifier "$tag-rec" -ErrorAction SilentlyContinue | ForEach-Object {
                    @{ text = $_.SourceEventArgs.Result.Text; conf = [Math]::Round($_.SourceEventArgs.Result.Confidence, 3) } })
                $reply = @{ id = $req.id; ok = $true; parts = $parts; complete = [bool]$done }
            } finally {
                Unregister-Event -SourceIdentifier "$tag-rec" -ErrorAction SilentlyContinue
                Unregister-Event -SourceIdentifier "$tag-done" -ErrorAction SilentlyContinue
                Remove-Event -SourceIdentifier "$tag-rec" -ErrorAction SilentlyContinue
                Remove-Event -SourceIdentifier "$tag-done" -ErrorAction SilentlyContinue
                $r.Dispose()          # releases the input WAV before we reply
            }
            Send $reply
        }
        else { Send @{ id = $req.id; ok = $false; error = "unknown op" } }
    }
    catch {
        $rid = if ($req) { $req.id } else { $null }
        Send @{ id = $rid; ok = $false; error = $_.Exception.Message }
    }
}
