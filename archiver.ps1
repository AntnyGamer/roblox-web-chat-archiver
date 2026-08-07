# Roblox Web Chat Archiver - 2026 compatibility refresh
# Windows PowerShell 5.1+ compatible; no third-party modules required.

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$AppName = 'Roblox Web Chat Archiver (2026 refresh)'
$ChatBase = 'https://apis.roblox.com/platform-chat-api/v1'
$UsersBase = 'https://users.roblox.com/v1'
$MinRequestIntervalMs = 520
$MaxRetries = 6
$script:LastRequestUtc = [DateTime]::UtcNow.AddSeconds(-5)
$script:WebSession = $null
$script:CsrfToken = $null

function Get-PropertyValue {
    param($Object, [string[]]$Names, $Default = $null)
    if ($null -eq $Object) { return $Default }
    foreach ($name in $Names) {
        $prop = $Object.PSObject.Properties[$name]
        if ($null -ne $prop) { return $prop.Value }
    }
    $dataProp = $Object.PSObject.Properties['data']
    if ($null -ne $dataProp -and $null -ne $dataProp.Value) {
        foreach ($name in $Names) {
            $prop = $dataProp.Value.PSObject.Properties[$name]
            if ($null -ne $prop) { return $prop.Value }
        }
    }
    return $Default
}

function Get-ItemArray {
    param($Data, [string[]]$Names)
    $value = Get-PropertyValue $Data $Names @()
    if ($null -eq $value) { return @() }
    return @($value)
}

function Get-ErrorDetail {
    param($Payload)
    if ($null -eq $Payload) { return 'Unknown Roblox API error' }
    $errors = Get-PropertyValue $Payload @('errors') $null
    if ($null -ne $errors -and @($errors).Count -gt 0) {
        $parts = @()
        foreach ($e in @($errors)) {
            $code = Get-PropertyValue $e @('code') $null
            $msg = Get-PropertyValue $e @('message','userFacingMessage') 'Unknown Roblox API error'
            if ($null -ne $code) { $parts += ("{0}: {1}" -f $code, $msg) } else { $parts += [string]$msg }
        }
        return ($parts -join '; ')
    }
    $msg = Get-PropertyValue $Payload @('message','Message','error','Error') $null
    if ($null -ne $msg) { return [string]$msg }
    return ([string]$Payload)
}

function Wait-RequestPacing {
    $elapsed = ([DateTime]::UtcNow - $script:LastRequestUtc).TotalMilliseconds
    if ($elapsed -lt $MinRequestIntervalMs) {
        Start-Sleep -Milliseconds ([int]($MinRequestIntervalMs - $elapsed))
    }
    $script:LastRequestUtc = [DateTime]::UtcNow
}

function Initialize-RobloxSession {
    param([Parameter(Mandatory=$true)][string]$CookieValue)

    $script:WebSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $script:WebSession.UserAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'

    $rbxCookie = New-Object System.Net.Cookie
    $rbxCookie.Name = '.ROBLOSECURITY'
    $rbxCookie.Value = $CookieValue
    $rbxCookie.Path = '/'
    $rbxCookie.Domain = '.roblox.com'
    $rbxCookie.Secure = $true
    $rbxCookie.HttpOnly = $true
    $script:WebSession.Cookies.Add($rbxCookie)
}

function Get-ResponseHeaderValue {
    param($Response, [string]$Name)
    if ($null -eq $Response) { return $null }
    try {
        $value = $Response.Headers[$Name]
        if ($null -ne $value) { return [string]$value }
    } catch {}
    return $null
}

function Invoke-RobloxRaw {
    param(
        [Parameter(Mandatory=$true)][string]$Url,
        [ValidateSet('GET','POST')][string]$Method = 'GET',
        [hashtable]$Query = @{},
        [switch]$AllowCsrfRetry
    )

    $pairs = @()
    foreach ($key in $Query.Keys) {
        $value = $Query[$key]
        if ($null -ne $value -and [string]$value -ne '') {
            $pairs += (([Uri]::EscapeDataString([string]$key)) + '=' + ([Uri]::EscapeDataString([string]$value)))
        }
    }
    if ($pairs.Count -gt 0) { $Url = $Url + '?' + ($pairs -join '&') }

    $headers = @{
        'Accept' = 'application/json, text/plain, */*'
        'Origin' = 'https://www.roblox.com'
        'Referer' = 'https://www.roblox.com/'
    }
    if ($script:CsrfToken) { $headers['X-CSRF-TOKEN'] = $script:CsrfToken }

    Wait-RequestPacing
    try {
        return Invoke-WebRequest -UseBasicParsing -Uri $Url -Method $Method -Headers $headers -WebSession $script:WebSession -TimeoutSec 30
    }
    catch {
        $response = $_.Exception.Response
        $status = $null
        if ($null -ne $response) { try { $status = [int]$response.StatusCode } catch {} }

        if ($AllowCsrfRetry -and $status -eq 403) {
            $token = Get-ResponseHeaderValue $response 'x-csrf-token'
            if (-not $token) { $token = Get-ResponseHeaderValue $response 'X-CSRF-TOKEN' }
            if ($token) {
                $script:CsrfToken = $token
                $headers['X-CSRF-TOKEN'] = $token
                Wait-RequestPacing
                return Invoke-WebRequest -UseBasicParsing -Uri $Url -Method $Method -Headers $headers -WebSession $script:WebSession -TimeoutSec 30
            }
        }
        throw
    }
}

function Refresh-RobloxSession {
    Write-Host 'Refreshing Roblox session cookie...'
    try {
        [void](Invoke-RobloxRaw -Url 'https://auth.roblox.com/v1/session/refresh' -Method POST -AllowCsrfRetry)
    }
    catch {
        $response = $_.Exception.Response
        $status = $null
        if ($null -ne $response) { try { $status = [int]$response.StatusCode } catch {} }
        if ($status -eq 401) {
            throw 'Roblox rejected this .ROBLOSECURITY session while refreshing it (HTTP 401). Copy the CURRENT cookie value from the Roblox tab again; Roblox may have rotated the value since you copied it.'
        }
        Write-Host ('  Session refresh was not accepted; continuing with the supplied cookie. (' + $_.Exception.Message + ')') -ForegroundColor Yellow
    }
}

function Invoke-RobloxJson {
    param(
        [Parameter(Mandatory=$true)][string]$Url,
        [hashtable]$Query = @{}
    )

    $lastError = $null
    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        try {
            $response = Invoke-RobloxRaw -Url $Url -Method GET -Query $Query
            $content = [string]$response.Content
            if ([string]::IsNullOrWhiteSpace($content)) { return $null }
            return ($content | ConvertFrom-Json)
        }
        catch {
            $lastError = $_.Exception
            $status = $null
            $body = ''
            $response = $_.Exception.Response
            if ($null -ne $response) {
                try { $status = [int]$response.StatusCode } catch {}
                try {
                    $stream = $response.GetResponseStream()
                    if ($null -ne $stream) {
                        $reader = New-Object System.IO.StreamReader($stream)
                        $body = $reader.ReadToEnd()
                        $reader.Dispose()
                    }
                } catch {}
            }

            $payload = $null
            if ($body) { try { $payload = $body | ConvertFrom-Json } catch { $payload = $body } }
            $detail = Get-ErrorDetail $payload

            if ($status -eq 401) {
                if ($attempt -eq 1) {
                    try { Refresh-RobloxSession; continue } catch {}
                }
                throw 'Roblox rejected the login session (HTTP 401). The cookie in the browser may have rotated since it was copied. Re-open DevTools, copy the CURRENT .ROBLOSECURITY value, and run the archiver again.'
            }
            if ($status -eq 403) {
                throw ("Roblox refused the authenticated request (HTTP 403). Roblox said: {0}" -f $detail)
            }
            if ($status -eq 429 -and $attempt -lt $MaxRetries) {
                $retryAfter = Get-ResponseHeaderValue $response 'Retry-After'
                $wait = 0
                if (-not [Int32]::TryParse([string]$retryAfter, [ref]$wait) -or $wait -lt 1) {
                    $wait = [int][Math]::Min(30, [Math]::Pow(2, $attempt))
                }
                Write-Host "`nRate limited by Roblox; retrying after $wait seconds..."
                Start-Sleep -Seconds $wait
                continue
            }
            if ($null -ne $status -and $status -ge 500 -and $status -le 599 -and $attempt -lt $MaxRetries) {
                $wait = [int][Math]::Min(20, [Math]::Pow(2, $attempt - 1))
                Write-Host "`nRoblox API returned HTTP $status; retrying in $wait seconds..."
                Start-Sleep -Seconds $wait
                continue
            }
            if ($null -ne $status) {
                throw ("Roblox API error HTTP {0}: {1} ({2})" -f $status, $detail, $Url)
            }
            if ($attempt -lt $MaxRetries) {
                $wait = [int][Math]::Min(20, [Math]::Pow(2, $attempt - 1))
                Write-Host "`nNetwork error; retrying in $wait seconds..."
                Start-Sleep -Seconds $wait
                continue
            }
        }
    }
    throw ("Could not reach Roblox after {0} attempts: {1}" -f $MaxRetries, $lastError.Message)
}

function Invoke-PublicRobloxJson {
    param(
        [Parameter(Mandatory=$true)][string]$Url,
        [ValidateSet('GET','POST')][string]$Method = 'GET',
        $Body = $null
    )

    $headers = @{
        'Accept' = 'application/json, text/plain, */*'
        'User-Agent' = 'RobloxWebChatArchiver/2026'
    }
    $lastError = $null

    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        try {
            if ($Method -eq 'POST') {
                $jsonBody = $Body | ConvertTo-Json -Depth 12 -Compress
                $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -Method POST -Headers $headers -ContentType 'application/json' -Body $jsonBody -TimeoutSec 30
            }
            else {
                $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -Method GET -Headers $headers -TimeoutSec 30
            }
            $content = [string]$response.Content
            if ([string]::IsNullOrWhiteSpace($content)) { return $null }
            return ($content | ConvertFrom-Json)
        }
        catch {
            $lastError = $_.Exception
            $status = $null
            $response = $_.Exception.Response
            if ($null -ne $response) { try { $status = [int]$response.StatusCode } catch {} }

            if ($status -eq 429 -and $attempt -lt $MaxRetries) {
                $retryAfter = Get-ResponseHeaderValue $response 'Retry-After'
                $wait = 0
                if (-not [Int32]::TryParse([string]$retryAfter, [ref]$wait) -or $wait -lt 1) {
                    $wait = [int][Math]::Min(20, [Math]::Pow(2, $attempt))
                }
                Start-Sleep -Seconds $wait
                continue
            }
            if ($null -ne $status -and $status -ge 500 -and $status -le 599 -and $attempt -lt $MaxRetries) {
                Start-Sleep -Seconds ([int][Math]::Min(10, [Math]::Pow(2, $attempt - 1)))
                continue
            }
            if ($null -eq $status -and $attempt -lt $MaxRetries) {
                Start-Sleep -Seconds ([int][Math]::Min(10, [Math]::Pow(2, $attempt - 1)))
                continue
            }
            throw
        }
    }
    throw ("Could not reach Roblox public API after {0} attempts: {1}" -f $MaxRetries, $lastError.Message)
}

function Get-NextCursor {
    param($Data)
    $value = Get-PropertyValue $Data @('next_cursor','nextCursor','nextPageCursor') $null
    if ($null -eq $value) { return $null }
    $text = ([string]$value).Trim()
    if ($text.Length -eq 0) { return $null }
    return $text
}

function Get-ConversationId {
    param($Convo)
    $value = Get-PropertyValue $Convo @('id','conversation_id','conversationId') $null
    if ($null -eq $value -or ([string]$value).Length -eq 0) { return $null }
    return [string]$value
}

function Get-AllConversations {
    $result = [ordered]@{}
    $cursor = $null
    $seen = @{}
    $page = 0

    while ($true) {
        $page++
        $query = @{}
        if ($cursor) { $query['cursor'] = $cursor }
        $data = Invoke-RobloxJson -Url "$ChatBase/get-user-conversations" -Query $query
        $items = Get-ItemArray $data @('conversations','Conversations','items')
        foreach ($item in $items) {
            $cid = Get-ConversationId $item
            if ($cid) { $result[$cid] = $item }
        }
        Write-Host ("  conversations page {0}: +{1} (total {2})" -f $page, $items.Count, $result.Count)

        $next = Get-NextCursor $data
        if (-not $next) { break }
        if ($seen.ContainsKey($next)) { throw 'Roblox returned the same conversation cursor twice; stopping to avoid an infinite loop.' }
        $seen[$next] = $true
        $cursor = $next
    }
    return $result
}

function Get-AllMessages {
    param([Parameter(Mandatory=$true)][string]$ConversationId)

    $all = New-Object System.Collections.ArrayList
    $cursor = $null
    $seen = @{}
    while ($true) {
        $query = @{ 'conversation_id' = $ConversationId }
        if ($cursor) { $query['cursor'] = $cursor }
        $data = Invoke-RobloxJson -Url "$ChatBase/get-conversation-messages" -Query $query
        $items = Get-ItemArray $data @('messages','Messages','items')
        foreach ($item in $items) { [void]$all.Add($item) }

        $next = Get-NextCursor $data
        if (-not $next) { break }
        if ($seen.ContainsKey($next)) { throw "Roblox returned the same message cursor twice for conversation $ConversationId." }
        $seen[$next] = $true
        $cursor = $next
    }
    return @($all)
}

function Get-MessageSenderId {
    param($Message)
    $value = Get-PropertyValue $Message @('sender_user_id','senderUserId','senderTargetId') $null
    if ($null -eq $value) {
        $sender = Get-PropertyValue $Message @('sender') $null
        $value = Get-PropertyValue $sender @('id','userId','targetId') $null
    }
    $parsed = 0L
    if ($null -ne $value -and [Int64]::TryParse([string]$value, [ref]$parsed)) { return $parsed }
    return 0L
}

function Get-MessageContent {
    param($Message)
    $value = Get-PropertyValue $Message @('content','text','message') ''
    if ($null -eq $value) { return '' }
    return [string]$value
}

function Get-MessageTime {
    param($Message)
    $value = Get-PropertyValue $Message @('created_at','createdAt','sent','timestamp') ''
    if ($null -eq $value) { return '' }
    return [string]$value
}

function Get-ParticipantIds {
    param($Convo)
    $raw = Get-PropertyValue $Convo @('participant_user_ids','participantUserIds','participants') @()
    $ids = @()
    foreach ($item in @($raw)) {
        $value = $item
        if ($item -isnot [string] -and $item -isnot [ValueType]) {
            $value = Get-PropertyValue $item @('id','userId','targetId') $null
        }
        $id = 0L
        if ($null -ne $value -and [Int64]::TryParse([string]$value, [ref]$id) -and $id -ne 0 -and $ids -notcontains $id) {
            $ids += $id
        }
    }
    return $ids
}

function Get-CreatedById {
    param($Convo)
    $value = Get-PropertyValue $Convo @('created_by','createdBy') $null
    if ($null -ne $value -and $value -isnot [string] -and $value -isnot [ValueType]) {
        $value = Get-PropertyValue $value @('id','userId','targetId') $null
    }
    $id = 0L
    if ($null -ne $value -and [Int64]::TryParse([string]$value, [ref]$id)) { return $id }
    return 0L
}

function Resolve-UsersBatch {
    param([Int64[]]$UserIds)

    $result = [ordered]@{}
    $unique = @($UserIds | Where-Object { $_ -ne 0 } | Sort-Object -Unique)
    if ($unique.Count -eq 0) { return $result }

    $batchSize = 100
    for ($start = 0; $start -lt $unique.Count; $start += $batchSize) {
        $end = [Math]::Min($start + $batchSize - 1, $unique.Count - 1)
        $batch = @($unique[$start..$end])
        Write-Host ("  resolving users {0}-{1} of {2}" -f ($start + 1), ($end + 1), $unique.Count)

        try {
            $data = Invoke-PublicRobloxJson -Url "$UsersBase/users" -Method POST -Body ([ordered]@{
                userIds = @($batch)
                excludeBannedUsers = $false
            })
            foreach ($user in (Get-ItemArray $data @('data','users','items'))) {
                $uidRaw = Get-PropertyValue $user @('id','userId','targetId') $null
                $uid = 0L
                if ($null -eq $uidRaw -or -not [Int64]::TryParse([string]$uidRaw, [ref]$uid) -or $uid -eq 0) { continue }
                $name = [string](Get-PropertyValue $user @('name') "Unknown user $uid")
                $display = [string](Get-PropertyValue $user @('displayName','name') $name)
                $verified = [bool](Get-PropertyValue $user @('hasVerifiedBadge') $false)
                $result[[string]$uid] = [ordered]@{ hasVerifiedBadge=$verified; name=$name; displayName=$display; targetId=$uid }
            }
        }
        catch {
            Write-Host ("  Batch user lookup failed; falling back for this batch. ({0})" -f $_.Exception.Message) -ForegroundColor Yellow
        }

        foreach ($uid in $batch) {
            if ($result.Contains([string]$uid)) { continue }
            try {
                $user = Invoke-PublicRobloxJson -Url "$UsersBase/users/$uid" -Method GET
                $name = [string](Get-PropertyValue $user @('name') "Unknown user $uid")
                $display = [string](Get-PropertyValue $user @('displayName','name') $name)
                $verified = [bool](Get-PropertyValue $user @('hasVerifiedBadge') $false)
                $result[[string]$uid] = [ordered]@{ hasVerifiedBadge=$verified; name=$name; displayName=$display; targetId=[Int64]$uid }
            }
            catch {
                $result[[string]$uid] = [ordered]@{ hasVerifiedBadge=$false; name="Unknown user $uid"; displayName="Unknown user $uid"; targetId=[Int64]$uid; _lookupError=$_.Exception.Message }
            }
        }
    }
    return $result
}

try {
    Write-Host ('=' * 62)
    Write-Host $AppName
    Write-Host ('=' * 62)
    Write-Host 'This program reads your existing Roblox website chat history and'
    Write-Host 'writes a local JSON archive. Your cookie is NOT saved to the file.'
    Write-Host 'Never paste your .ROBLOSECURITY value into chats or websites.'
    Write-Host ''

    $secure = Read-Host 'Paste your .ROBLOSECURITY value' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $cookie = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }

    $cookie = $cookie.Trim().Trim('"').Trim("'")
    $cookie = [Regex]::Replace($cookie, '^\s*\.ROBLOSECURITY\s*=\s*', '', 'IgnoreCase')
    if ($cookie.Contains(';')) { $cookie = $cookie.Split(';')[0].Trim() }
    if (-not $cookie) { throw 'No .ROBLOSECURITY value was entered.' }
    Initialize-RobloxSession -CookieValue $cookie
    Remove-Variable cookie -ErrorAction SilentlyContinue

    Write-Host "`nPreparing Roblox session..."
    Refresh-RobloxSession
    Write-Host 'Checking Roblox login...'
    $me = Invoke-RobloxJson -Url "$UsersBase/users/authenticated"
    $myId = [Int64](Get-PropertyValue $me @('id') 0)
    if ($myId -eq 0) { throw ('Roblox did not return a valid authenticated user: ' + (Get-ErrorDetail $me)) }
    $myName = [string](Get-PropertyValue $me @('name') 'Unknown')
    Write-Host "Logged in as $myName (ID $myId)."

    Write-Host "`nFetching conversations..."
    $rawConversations = Get-AllConversations
    Write-Host ("Found {0} conversations.`n" -f $rawConversations.Count)

    $normalized = [ordered]@{}
    $ids = @{}
    $ids[[string]$myId] = $myId
    $failures = New-Object System.Collections.ArrayList
    $i = 0

    foreach ($entry in $rawConversations.GetEnumerator()) {
        $i++
        $cid = [string]$entry.Key
        $convo = $entry.Value
        $title = [string](Get-PropertyValue $convo @('name','title','displayName') $cid)
        Write-Host -NoNewline ("[{0}/{1}] Fetching {2}... " -f $i, $rawConversations.Count, $title)

        $creator = Get-CreatedById $convo
        if ($creator -ne 0) { $ids[[string]$creator] = $creator }
        foreach ($participantId in @(Get-ParticipantIds $convo)) {
            if ($participantId -ne 0) { $ids[[string]$participantId] = $participantId }
        }

        $messages = @()
        $archiveStatus = $null
        $archiveError = $null
        try {
            $messages = Get-AllMessages -ConversationId $cid
        }
        catch {
            $conversationError = $_.Exception.Message
            if ($conversationError -match '(?i)HTTP 401|HTTP 403|rejected the login session|refused the authenticated request') {
                throw
            }
            $archiveStatus = 'metadata-only-error'
            $archiveError = $conversationError
            [void]$failures.Add([ordered]@{ conversationId=$cid; title=$title; error=$conversationError })
        }

        if (-not $archiveStatus -and @($messages).Count -eq 0) {
            $archiveStatus = 'metadata-only-empty-response'
        }

        $outMessages = New-Object System.Collections.ArrayList
        foreach ($message in @($messages)) {
            $senderId = Get-MessageSenderId $message
            $obj = [ordered]@{}
            foreach ($property in $message.PSObject.Properties) {
                if ($property.Name -notin @('sender_user_id','senderUserId','created_at','createdAt')) {
                    $obj[$property.Name] = $property.Value
                }
            }
            $obj['senderTargetId'] = $senderId
            $obj['sent'] = Get-MessageTime $message
            $obj['content'] = Get-MessageContent $message
            [void]$outMessages.Add($obj)
            if ($senderId -ne 0) { $ids[[string]$senderId] = $senderId }
        }

        $outConvo = [ordered]@{}
        foreach ($property in $convo.PSObject.Properties) {
            if ($property.Name -ne 'name') { $outConvo[$property.Name] = $property.Value }
        }
        $outConvo['id'] = Get-PropertyValue $convo @('id') $cid
        $outConvo['title'] = $title
        $outConvo['messages'] = @($outMessages)
        if ($archiveStatus) { $outConvo['_archiveStatus'] = $archiveStatus }
        if ($archiveError) { $outConvo['_archiveError'] = $archiveError }
        $normalized[$cid] = $outConvo

        if ($archiveError) {
            Write-Host ("FAILED; metadata saved ({0})" -f $archiveError)
        }
        elseif (@($messages).Count -eq 0) {
            Write-Host '0 messages; metadata saved'
        }
        else {
            Write-Host ("{0} messages" -f @($messages).Count)
        }
    }

    Write-Host ("`nResolving {0} users in batches..." -f $ids.Count)
    $people = Resolve-UsersBatch -UserIds @($ids.Values)
    $myDisplay = [string](Get-PropertyValue $me @('displayName','name') $myName)
    $myVerified = [bool](Get-PropertyValue $me @('hasVerifiedBadge') $false)
    $people[[string]$myId] = [ordered]@{ hasVerifiedBadge=$myVerified; name=$myName; displayName=$myDisplay; targetId=$myId }
    $people['0'] = [ordered]@{ hasVerifiedBadge=$false; name='Roblox'; displayName='Roblox'; targetId=0 }

    $metadataOnly = 0
    $messageCount = 0
    foreach ($conversation in $normalized.Values) {
        $count = @($conversation['messages']).Count
        $messageCount += $count
        if ($count -eq 0) { $metadataOnly++ }
    }

    $payload = [ordered]@{
        userId = $myId
        people = $people
        conversations = $normalized
        archiveMeta = [ordered]@{
            createdAt = [DateTime]::UtcNow.ToString('o')
            archiver = $AppName
            conversationCount = $normalized.Count
            metadataOnlyConversations = $metadataOnly
            failedConversations = @($failures)
        }
    }

    $safeName = [Regex]::Replace($myName, '[^A-Za-z0-9 _-]', '')
    if (-not $safeName) { $safeName = 'user' }
    $stamp = Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'
    $path = Join-Path (Get-Location) ("roblox-chat-archive-{0}-{1}-{2}.json" -f $myId, $safeName, $stamp)
    $tmp = $path + '.tmp'
    $json = $payload | ConvertTo-Json -Depth 100
    [IO.File]::WriteAllText($tmp, $json, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $path -Force

    Write-Host "`nDone."
    Write-Host "Saved: $path"
    Write-Host ("Archived conversations: {0}" -f $normalized.Count)
    Write-Host ("Archived messages: {0}" -f $messageCount)
    if ($metadataOnly -gt 0) { Write-Host ("Metadata-only conversations: {0}" -f $metadataOnly) }
    if ($failures.Count -gt 0) {
        Write-Host ("WARNING: {0} conversation(s) had message-fetch errors; their metadata was still saved." -f $failures.Count)
    }
}
catch {
    $errorPath = Join-Path (Get-Location) 'archiver-error.txt'
    $message = $_.Exception.Message
    try {
        $text = $AppName + "`r`nTime: " + [DateTime]::UtcNow.ToString('o') + "`r`n`r`n" + ($_ | Out-String)
        $cookieVar = Get-Variable -Name cookie -ErrorAction SilentlyContinue
        if ($null -ne $cookieVar -and -not [string]::IsNullOrEmpty([string]$cookieVar.Value)) {
            $text = $text.Replace([string]$cookieVar.Value, '[REDACTED]')
        }
        [IO.File]::WriteAllText($errorPath, $text, (New-Object Text.UTF8Encoding($false)))
    } catch {}
    Write-Host "`nERROR:" -ForegroundColor Red
    Write-Host $message -ForegroundColor Red
    Write-Host "`nA diagnostic error was written to: $errorPath"
    Write-Host 'The diagnostic file does NOT contain the cookie value.'
    exit 1
}
finally {
    Remove-Variable cookie -ErrorAction SilentlyContinue
    Remove-Variable secure -ErrorAction SilentlyContinue
    $script:CsrfToken = $null
    $script:WebSession = $null
}
