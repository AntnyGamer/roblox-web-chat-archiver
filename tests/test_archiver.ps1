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
if ($script:LastQuery.pageSize -ne 30) { throw 'Conversation requests must ask for 30 items per page' }
$script:Responses = @($fixture.messages)
$script:ResponseIndex = 0
$messages = @(Get-AllMessages -ConversationId 'first')
if ($messages.Count -ne 2 -or $messages[1].content -ne 'last message') { throw 'Final message page was dropped' }
if ($script:LastQuery.pageSize -ne 30) { throw 'Message requests must ask for 30 items per page' }
$script:Responses = @([pscustomobject]@{ conversations=@([pscustomobject]@{ id='saved-chat' }); nextCursor='next' }, [pscustomobject]@{ error='bad page' })
$script:ResponseIndex = 0
$partialList = $false
try { $null = Get-AllConversations }
catch {
    $partial = $_.Exception.Data['PartialConversations']
    $partialList = $_.Exception.Message -match 'Malformed Roblox page' -and $null -ne $partial -and $partial.Contains('saved-chat')
}
if (-not $partialList) { throw 'Later conversation-list failures must preserve earlier conversation IDs' }
$script:Responses = @([pscustomobject]@{ conversations=@([pscustomobject]@{ name='missing ID' }) })
$script:ResponseIndex = 0
$missingIdRejected = $false
try { $null = Get-AllConversations } catch { $missingIdRejected = $_.Exception.Message -match 'valid ID' }
if (-not $missingIdRejected) { throw 'Conversation records without IDs must not be silently skipped' }
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
try { $null = Get-AllConversations } catch { $invalidMembers = $_.Exception.Message -match 'valid ID' }
if (-not $invalidMembers) { throw 'Malformed conversation entries must not be skipped' }
$nestedUsers = @(Get-ItemArray ([pscustomobject]@{ data=[pscustomobject]@{ users=@([pscustomobject]@{ id=89 }) } }) @('data','users','items'))
if ($nestedUsers.Count -ne 1 -or $nestedUsers[0].id -ne 89) { throw 'Nested user batches should avoid individual lookups' }
$script:Responses = @([pscustomobject]@{ conversations=@(); nextCursor='same' }, [pscustomobject]@{ conversations=@(); nextCursor='same' })
$script:ResponseIndex = 0
$loopStopped = $false
try { $null = Get-AllConversations } catch { $loopStopped = $_.Exception.Message -match 'cursor' }
if (-not $loopStopped) { throw 'Repeated cursor was not rejected' }
if ((Get-NextCursor ([pscustomobject]@{ nextCursor='xyz' })) -ne 'xyz') { throw 'Cursor parsing failed' }
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
if ($source -match 'Move-Item -LiteralPath \$tmp -Destination \$path -Force') {
    throw 'Final archive move must not forcibly replace an existing backup'
}
if ($source -notmatch '\[Guid\]::NewGuid\(\)') {
    throw 'Archive filenames must have a unique suffix'
}
Write-Host 'PASS: PowerShell syntax, final pages, and cursor loop safeguards'
