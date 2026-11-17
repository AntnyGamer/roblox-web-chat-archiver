# Regression-test the PowerShell implementation without running its interactive entrypoint.
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot '..\archiver.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw "PowerShell syntax errors: $($parseErrors -join '; ')" }
# Load only function definitions so no Roblox credentials, prompts or requests are used.
$definitions = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
foreach ($definition in $definitions) { . ([scriptblock]::Create($definition.Extent.Text)) }
# Roblox may return null primary fields but usable alias or nested response fields.
$nullAlias = [pscustomobject]@{ content=$null; text='preserved text' }
if ((Get-MessageContent $nullAlias) -ne 'preserved text') { throw 'Null message content hid text alias' }
if ((Get-MessageSenderId ([pscustomobject]@{ sender_user_id=$null; senderUserId=51 })) -ne 51) {
    throw 'Null sender ID hid valid sender alias'
}
if ((Get-PropertyValue ([pscustomobject]@{ content=$null; data=[pscustomobject]@{ content='nested' } }) @('content') '') -ne 'nested') {
    throw 'Null outer field hid usable nested response data'
}
if ((Get-PropertyValue ([pscustomobject]@{ hasVerifiedBadge=$false }) @('hasVerifiedBadge') $true) -ne $false) {
    throw 'False-valued fields must not be treated as null'
}
$ChatBase = 'https://example.invalid/fixture'
$fixture = Get-Content (Join-Path $PSScriptRoot 'fixtures\pagination.json') -Raw | ConvertFrom-Json
$script:Responses = @($fixture.conversations)
$script:ResponseIndex = 0
function Invoke-RobloxJson {
    param($Url, $Query)
    if ($script:ResponseIndex -ge $script:Responses.Count) { throw 'Unexpected extra fixture request' }
    $response = $script:Responses[$script:ResponseIndex]
    $script:ResponseIndex++
    $script:LastQuery = $Query
    return $response
}
$conversations = Get-AllConversations
if ($conversations.Count -ne 2 -or -not $conversations.Contains('first') -or -not $conversations.Contains('last')) { throw 'Final conversation page was dropped' }
if ($script:ResponseIndex -ne 2) { throw 'Wrong conversation request count' }
if ($script:LastQuery.pageSize -ne 30 -or $script:LastQuery.include_user_data -ne 'true') {
    throw 'Conversation requests must ask for 30 items per page and user data'
}
$script:Responses = @($fixture.messages)
$script:ResponseIndex = 0
$messages = @(Get-AllMessages -ConversationId 'first')
if ($messages.Count -ne 2 -or $messages[1].content -ne 'last message') { throw 'Final message page was dropped' }
if ($script:LastQuery.pageSize -ne 30) { throw 'Message requests must ask for 30 items per page' }
$script:Responses = @(
    [pscustomobject]@{ messages=@([pscustomobject]@{ id='same'; content='A' }); nextCursor='another' },
    [pscustomobject]@{ messages=@([pscustomobject]@{ id='same'; content='A' }, [pscustomobject]@{ id='different'; content='B' }) }
)
$script:ResponseIndex = 0
$uniqueMessages = @(Get-AllMessages -ConversationId 'test')
if ($uniqueMessages.Count -ne 2 -or $uniqueMessages[1].id -ne 'different') { throw 'Overlapping message pages were not deduplicated' }
$cached = Get-EmbeddedPeople -Conversations @([pscustomobject]@{
    user_data=[pscustomobject]@{
        '99'=[pscustomobject]@{ id=99; name='Known'; display_name='Friend'; is_verified=$true }
        '100'=[pscustomobject]@{ id=100; name='Incomplete'; display_name='' }
    }
})
if ($cached.Count -ne 1 -or -not $cached.Contains('99') -or
    $cached['99'].name -ne 'Known' -or $cached['99'].displayName -ne 'Friend' -or
    -not $cached['99'].hasVerifiedBadge) {
    throw 'Embedded user profiles should be reused only when complete'
}
$script:Responses = @(
    [pscustomobject]@{ conversations=@([pscustomobject]@{ id=$null; source='friends'; type='one_to_one'; participant_user_ids=@(21,99); name='Friend' }, [pscustomobject]@{ id='a' }); nextCursor='more' },
    [pscustomobject]@{ conversations=@([pscustomobject]@{ id='b' }) },
    [pscustomobject]@{ conversations=@([pscustomobject]@{ id='a'; name='Updated' }); nextCursor='more' },
    [pscustomobject]@{ conversations=@([pscustomobject]@{ id=$null; source='friends'; type='one_to_one'; participant_user_ids=@(99,21); name='Friend' }, [pscustomobject]@{ id='c' }) }
)
$script:ResponseIndex = 0
$verified = Get-AllConversations -Verify
if ($verified.Count -ne 4 -or -not $verified.Contains('a') -or -not $verified.Contains('b') -or
    -not $verified.Contains('c') -or -not $verified.Contains('__missing-id-p1-i1')) {
    throw 'Verification passes did not preserve the union of ID-less friends and real chats'
}
if ($verified['a'].name -ne 'Updated' -or $script:ResponseIndex -ne 4) { throw 'Verification did not merge the second pass correctly' }
$script:Responses = @(
    [pscustomobject]@{ conversations=@([pscustomobject]@{ id='chat'; messages=@(
        [pscustomobject]@{ id='first'; content='old'; meta=[pscustomobject]@{ sender='Alice' } },
        [pscustomobject]@{ id='second'; content='second' }) }); nextCursor='next' },
    [pscustomobject]@{ conversations=@([pscustomobject]@{ id='chat'; messages=@(
        [pscustomobject]@{ id='first'; content=$null; meta=[pscustomobject]@{ sent='2026' } },
        [pscustomobject]@{ id='third'; content='third' }) }) }
)
$script:ResponseIndex = 0
$unionMessages = (Get-AllConversations)['chat'].messages
if ($unionMessages.Count -ne 3 -or ($unionMessages.id -join ',') -ne 'first,second,third' -or
    $unionMessages[0].content -ne 'old' -or $unionMessages[0].meta.sender -ne 'Alice' -or
    $unionMessages[0].meta.sent -ne '2026') {
    throw 'Overlapping inventory passes must retain distinct embedded messages'
}
$multiplicity = (Merge-ConversationSnapshots -Snapshots @(
    ([ordered]@{ chat=[pscustomobject]@{ id='chat'; messages=@([pscustomobject]@{ content='same' }) } }),
    ([ordered]@{ chat=[pscustomobject]@{ id='chat'; messages=@([pscustomobject]@{ content='same' },
                                                               [pscustomobject]@{ content='same' }) } })
))['chat'].messages
if (@($multiplicity).Count -ne 2) {
    throw 'Identical ID-less messages in the same snapshot must preserve their multiplicity'
}
$script:Responses = @(
    [pscustomobject]@{ conversations=@([pscustomobject]@{ id='chat'; participant_user_ids=@(12,21) }); nextCursor='next' },
    [pscustomobject]@{ conversations=@([pscustomobject]@{ id='chat'; participant_user_ids=@(21,99) }) }
)
$script:ResponseIndex = 0
$members = Get-AllConversations
if (($members['chat'].participant_user_ids -join ',') -ne '12,21,99') {
    throw 'Repeated pages may not erase existing participant IDs'
}
$separateGroups = Merge-ConversationSnapshots -Snapshots @(
    ([ordered]@{ '__missing-id-p1-i1'=[pscustomobject]@{ source='friends'; type='group'; participant_user_ids=@(12,21); created_at='2025' } }),
    ([ordered]@{ '__missing-id-p1-i1'=[pscustomobject]@{ source='friends'; type='group'; participant_user_ids=@(12,21); created_at='2026' } })
)
if ($separateGroups.Count -ne 2) { throw 'Different ID-less group chats cannot be collapsed by member set' }
$script:Responses = @(
    [pscustomobject]@{ conversations=@([pscustomobject]@{
        id='same'; user_data=[pscustomobject]@{
            '99'=[pscustomobject]@{ display_name='Best friend'; is_verified=$true }
        }
        preview_message=[pscustomobject]@{ content='Original'; sent='old' }
    }); nextCursor='next' },
    [pscustomobject]@{ conversations=@([pscustomobject]@{
        id='same'; user_data=[pscustomobject]@{
            '99'=[pscustomobject]@{ display_name=$null; combined_name='Friend' }
        }
        preview_message=[pscustomobject]@{ content=$null; sent='new' }
    }) }
)
$script:ResponseIndex = 0
$combinedMetadata = Get-AllConversations
$profile = $combinedMetadata['same'].user_data.'99'
if ($combinedMetadata.Count -ne 1 -or $profile.display_name -ne 'Best friend' -or
    -not $profile.is_verified -or $profile.combined_name -ne 'Friend' -or
    $combinedMetadata['same'].preview_message.content -ne 'Original' -or
    $combinedMetadata['same'].preview_message.sent -ne 'old') {
    throw 'Repeated conversations must preserve nested user details and previews'
}
$script:Responses = @(
    [pscustomobject]@{ conversations=@([pscustomobject]@{ id='chat'; preview_message=[pscustomobject]@{
        id='old'; content='old'; sent='2020' } }) },
    [pscustomobject]@{ conversations=@([pscustomobject]@{ id='chat'; preview_message=[pscustomobject]@{
        id='new'; content='new'; sent='2026' } }) }
)
$script:ResponseIndex = 0
$previewReplacement = (Merge-ConversationSnapshots -Snapshots @(
    ([ordered]@{ chat=[pscustomobject]@{ id='chat'; preview_message=[pscustomobject]@{ id='old'; content='old'; sent='2020' } } }),
    ([ordered]@{ chat=[pscustomobject]@{ id='chat'; preview_message=[pscustomobject]@{ id='new'; content='new'; sent='2026' } } })
))['chat'].preview_message
if ($previewReplacement.content -ne 'new' -or $previewReplacement.sent -ne '2026' -or
    $previewReplacement.id -ne 'new') {
    throw 'Different previews must remain atomic rather than mixing text and timestamps'
}
$script:Responses = @(
    [pscustomobject]@{ messages=@([pscustomobject]@{ id='same'; content='Original'; senderUserId=12 }); nextCursor='next' },
    [pscustomobject]@{ messages=@([pscustomobject]@{ id='same'; content=$null; senderUserId=$null; extra='New' }) }
)
$script:ResponseIndex = 0
$enriched = @(Get-AllMessages -ConversationId 'chat')
if ($enriched.Count -ne 1 -or $enriched[0].content -ne 'Original' -or
    $enriched[0].extra -ne 'New' -or $enriched[0].senderUserId -ne 12) {
    throw 'Duplicate message IDs must preserve richer fields from either page'
}
$emptyPreviewFallback = Get-PreviewMessage ([pscustomobject]@{
    preview_message=[pscustomobject]@{};
    previewMessage=[pscustomobject]@{ id='available'; content='preserved' }
})
if ($emptyPreviewFallback.id -ne 'available') {
    throw 'An empty preview object must not conceal a usable alternate preview'
}
$previewForKnown = Get-PreviewMessage ([pscustomobject]@{
    preview_message=$null; previewMessage=[pscustomobject]@{ content='Accessible preview' }
})
if ($previewForKnown.content -ne 'Accessible preview') {
    throw 'The preview fallback must handle nullable primary fields'
}
$previewSource = Get-Content -LiteralPath $scriptPath -Raw
if ($previewSource -notmatch "metadata-only-error','metadata-only-empty-response" -or
    $previewSource -notmatch "archiveStatus = 'preview-only'") {
    throw 'Known-ID chats must preserve available message previews on empty or failed fetches'
}
$previewCountSource = Get-Content -LiteralPath $scriptPath -Raw
if ($previewCountSource -notmatch 'previewOnlyConversations') {
    throw 'PowerShell export must report preview-only conversations separately'
}
$script:Responses = @([pscustomobject]@{ conversations=@([pscustomobject]@{ id='saved-chat' }); nextCursor='next' }, [pscustomobject]@{ error='bad page' })
$script:ResponseIndex = 0
$partialList = $false
try { $null = Get-AllConversations }
catch {
    $partial = $_.Exception.Data['PartialConversations']
    $partialList = $_.Exception.Message -match 'Malformed Roblox page' -and $null -ne $partial -and $partial.Contains('saved-chat')
}
if (-not $partialList) { throw 'Later conversation-list failures must preserve earlier conversation IDs' }
$script:Responses = @(
    [pscustomobject]@{ conversations=@(
        [pscustomobject]@{ id=$null; name='Missing ID'; messages=@([pscustomobject]@{ content='preview' }) },
        [pscustomobject]@{ id='known'; name='Known' }
    ); nextCursor='next' },
    [pscustomobject]@{ conversations=@([pscustomobject]@{ name='Another missing ID' }) }
)
$script:ResponseIndex = 0
$idless = Get-AllConversations
if ($idless.Count -ne 3 -or -not $idless.Contains('__missing-id-p1-i1') -or
    -not $idless.Contains('__missing-id-p2-i1') -or -not $idless.Contains('known') -or
    $idless['__missing-id-p1-i1'].messages[0].content -ne 'preview') {
    throw 'Nullable conversation IDs must retain metadata and embedded preview messages'
}
if ($script:ResponseIndex -ne 2 -or $script:LastQuery.include_user_data -ne 'true') { throw 'Nullable IDs must not interrupt pagination' }
if ((Get-ConversationId ([pscustomobject]@{ id=$null; conversationId='other-id' })) -ne 'other-id') {
    throw 'Conversation ID lookup must fall back when the primary ID is null'
}
$sourceForMissingId = Get-Content -LiteralPath $scriptPath -Raw
if ($sourceForMissingId -notmatch "preview_message','previewMessage" -or
    $sourceForMissingId -notmatch 'archiveStatus = ''missing-id''') {
    throw 'ID-less conversation archiving must preserve embedded previews and mark missing IDs'
}
$script:Responses = @([pscustomobject]@{ messages=@([pscustomobject]@{ content='saved message' }); nextCursor='page-2' })
$script:ResponseIndex = 0
$partialSaved = $false
try { $null = Get-AllMessages -ConversationId 'first' }
catch {
    $partial = @($_.Exception.Data['PartialMessages'])
    $partialSaved = $partial.Count -eq 1 -and $partial[0].content -eq 'saved message'
}
if (-not $partialSaved) { throw 'A later page error discarded previously fetched messages' }
$script:Responses = @([pscustomobject]@{ messages=@([pscustomobject]@{ content='preserved' }); nextCursor='next' }, [pscustomobject]@{ message='bad page' })
$script:ResponseIndex = 0
$malformedPartial = $false
try { $null = Get-AllMessages -ConversationId 'first' }
catch {
    $partial = @($_.Exception.Data['PartialMessages'])
    $malformedPartial = $_.Exception.Message -match 'Malformed Roblox page' -and $partial.Count -eq 1 -and $partial[0].content -eq 'preserved'
}
if (-not $malformedPartial) { throw 'Malformed later message pages must preserve earlier messages' }
$script:Responses = @([pscustomobject]@{ messages=@([pscustomobject]@{ content='saved' }); nextCursor='next' }, [pscustomobject]@{ messages=@('unexpected string') })
$script:ResponseIndex = 0
$malformedMember = $false
try { $null = Get-AllMessages -ConversationId 'first' }
catch {
    $partial = @($_.Exception.Data['PartialMessages'])
    $malformedMember = $_.Exception.Message -match 'items must be objects' -and $partial.Count -eq 1 -and $partial[0].content -eq 'saved'
}
if (-not $malformedMember) { throw 'Malformed message entries must not be accepted or discard earlier pages' }
$script:Responses = @([pscustomobject]@{ message='bad conversations page' })
$script:ResponseIndex = 0
$invalidConversations = $false
try { $null = Get-AllConversations } catch { $invalidConversations = $_.Exception.Message -match 'Malformed Roblox page' }
if (-not $invalidConversations) { throw 'Malformed conversation pages must not silently look empty' }
$script:Responses = @([pscustomobject]@{ conversations=@('unexpected string') })
$script:ResponseIndex = 0
$invalidMembers = $false
try { $null = Get-AllConversations } catch { $invalidMembers = $_.Exception.Message -match 'items must be objects' }
if (-not $invalidMembers) { throw 'Malformed conversation entries must not be skipped' }
$nestedUsers = @(Get-ItemArray ([pscustomobject]@{ data=[pscustomobject]@{ users=@([pscustomobject]@{ id=89 }) } }) @('data','users','items'))
if ($nestedUsers.Count -ne 1 -or $nestedUsers[0].id -ne 89) { throw 'Nested user batches should avoid individual lookups' }
$script:Responses = @([pscustomobject]@{ conversations=@(); nextCursor='same' }, [pscustomobject]@{ conversations=@(); nextCursor='same' })
$script:ResponseIndex = 0
$loopStopped = $false
try { $null = Get-AllConversations } catch { $loopStopped = $_.Exception.Message -match 'cursor' }
if (-not $loopStopped) { throw 'Repeated cursor was not rejected' }
if ((Get-NextCursor ([pscustomobject]@{ nextCursor='xyz' })) -ne 'xyz') { throw 'Cursor parsing failed' }
if ((Get-NextCursor ([pscustomobject]@{ next_cursor=$null; nextCursor='fallback' })) -ne 'fallback') {
    throw 'Null cursor alias must not prevent fetching later pages'
}
$previewAlias = Get-PreviewMessage ([pscustomobject]@{
    preview_message=$null; previewMessage=[pscustomobject]@{ content='retained preview' }
})
if ($null -eq $previewAlias -or $previewAlias.content -ne 'retained preview') {
    throw 'Null preview alias must not hide available conversation preview'
}
if ((Get-RetrySeconds '999999999' 4) -ne 120 -or
    (Get-RetrySeconds 'NaN' 4) -ne 4 -or
    (Get-RetrySeconds 'Infinity' 4) -ne 4 -or
    (Get-RetrySeconds '-1' 4) -ne 4 -or
    (Get-RetrySeconds '1.4' 4) -ne 2) { throw 'Retry-After limits or invalid numbers failed' }
$participants = @(Get-ParticipantIds ([pscustomobject]@{ participants = @(6, 4, 6, 'bad', 0, [pscustomobject]@{ targetId=2 }, 4) }))
if (($participants -join ',') -ne '6,4,2') { throw 'Participant deduplication changed order or failed' }
$numericTime = Get-MessageTime ([pscustomobject]@{ timestamp = 1700000000 })
$numericMs = Get-MessageTime ([pscustomobject]@{ timestamp = 1700000000000L })
if ($numericTime -ne '2023-11-14T22:13:20.0000000Z' -or $numericMs -ne $numericTime) { throw 'Unix timestamps were not normalized consistently' }
$isoTime = Get-MessageTime ([pscustomobject]@{ timestamp = [DateTime]::SpecifyKind([DateTime]::Parse('2026-01-01T00:00:00'),[DateTimeKind]::Utc) })
if ($isoTime -ne '2026-01-01T00:00:00.0000000Z') { throw 'PowerShell DateTime values must serialize as portable UTC dates' }
$source = Get-Content $scriptPath -Raw
if ($source -match 'Move-Item -LiteralPath \$tmp -Destination \$Path -Force') {
    throw 'Final archive move must not forcibly replace an existing backup'
}
if ($source -notmatch '\[Guid\]::NewGuid\(\)') {
    throw 'Archive filenames must have a unique suffix'
}
$backupTestDir = Join-Path $env:TEMP ('roblox-backup-test-' + [Guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $backupTestDir)
try {
    $backupPath = Join-Path $backupTestDir 'success.json'
    Save-ArchiveJson -Path $backupPath -Json '{"content":"saved"}'
    if ((Get-Content -LiteralPath $backupPath -Raw) -ne '{"content":"saved"}') {
        throw 'Archive helper did not write the expected contents'
    }
    if (Test-Path -LiteralPath ($backupPath + '.tmp')) { throw 'Temporary archive remains after successful save' }
    function Move-Item { throw 'Simulated move failure' }
    try {
        $failedPath = Join-Path $backupTestDir 'failed.json'
        $caught = $false
        try { Save-ArchiveJson -Path $failedPath -Json '{"content":"private"}' }
        catch { $caught = $_.Exception.Message -match 'Simulated move failure' }
        if (-not $caught) { throw 'Archive helper swallowed a move failure' }
        if (Test-Path -LiteralPath ($failedPath + '.tmp')) { throw 'Private temporary archive remains after failed move' }
    }
    finally { Remove-Item Function:Move-Item -ErrorAction SilentlyContinue }
}
finally { Remove-Item -LiteralPath $backupTestDir -Recurse -Force -ErrorAction SilentlyContinue }
# Restore the real request function after earlier pagination tests stubbed it.
$realJsonRequest = $definitions | Where-Object { $_.Name -eq 'Invoke-RobloxJson' } | Select-Object -First 1
. ([scriptblock]::Create($realJsonRequest.Extent.Text))
$MaxRetries = 6
$script:MalformedCalls = 0
$script:UnexpectedSleeps = 0
function Start-Sleep { $script:UnexpectedSleeps++ }
function Invoke-RobloxRaw {
    $script:MalformedCalls++
    return [pscustomobject]@{ Content='{not valid json' }
}
function Invoke-WebRequest {
    $script:MalformedCalls++
    return [pscustomobject]@{ Content='{not valid json' }
}
try {
    $authenticatedError = ''
    try { $null = Invoke-RobloxJson -Url 'https://example.invalid/fixture' }
    catch { $authenticatedError = $_.Exception.Message }
    if ($authenticatedError -notmatch 'non-JSON' -or $script:MalformedCalls -ne 1 -or $script:UnexpectedSleeps -ne 0) {
        throw "Authenticated malformed JSON should fail fast (calls=$($script:MalformedCalls), sleeps=$($script:UnexpectedSleeps), error=$authenticatedError)"
    }
    $publicError = ''
    try { $null = Invoke-PublicRobloxJson -Url 'https://example.invalid/fixture' }
    catch { $publicError = $_.Exception.Message }
    if ($publicError -notmatch 'non-JSON' -or $script:MalformedCalls -ne 2 -or $script:UnexpectedSleeps -ne 0) {
        throw "Public malformed JSON should fail fast (calls=$($script:MalformedCalls), sleeps=$($script:UnexpectedSleeps), error=$publicError)"
    }
}
finally {
    Remove-Item Function:Invoke-RobloxRaw,Function:Invoke-WebRequest,Function:Start-Sleep -ErrorAction SilentlyContinue
}
Write-Host 'PASS: PowerShell syntax, paging, malformed JSON, and archive cleanup'
