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
    return $response
}
$conversations = Get-AllConversations
if ($conversations.Count -ne 2 -or -not $conversations.Contains('first') -or -not $conversations.Contains('last')) { throw 'Final conversation page was dropped' }
if ($script:ResponseIndex -ne 2) { throw 'Wrong conversation request count' }
$script:Responses = @($fixture.messages)
$script:ResponseIndex = 0
$messages = @(Get-AllMessages -ConversationId 'first')
if ($messages.Count -ne 2 -or $messages[1].content -ne 'last message') { throw 'Final message page was dropped' }
$script:Responses = @([pscustomobject]@{ conversations=@(); nextCursor='same' }, [pscustomobject]@{ conversations=@(); nextCursor='same' })
$script:ResponseIndex = 0
$loopStopped = $false
try { $null = Get-AllConversations } catch { $loopStopped = $_.Exception.Message -match 'cursor' }
if (-not $loopStopped) { throw 'Repeated cursor was not rejected' }
if ((Get-NextCursor ([pscustomobject]@{ nextCursor='xyz' })) -ne 'xyz') { throw 'Cursor parsing failed' }
Write-Host 'PASS: PowerShell syntax, final pages, and cursor loop safeguards'
