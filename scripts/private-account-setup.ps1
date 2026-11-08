$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $taskSetup = [Console]::In.ReadLine() | ConvertFrom-Json
    $taskUrl = [uri]$taskSetup.url
    if ($taskUrl.Scheme -ne 'https' -or $taskUrl.Query -or $taskUrl.UserInfo -or $taskUrl.AbsolutePath -notin @('/','')) { throw 'Invalid service origin' }
    $taskBase = $taskSetup.url.TrimEnd('/') + '/api'
    $taskCredentials = @{email=$taskSetup.email;password=$taskSetup.password}
    $taskRegistration = @{email=$taskSetup.email;password=$taskSetup.password;name='小伴管理员';timezone='Asia/Shanghai';consent_health_data=$true}
    $taskResponse = Invoke-WebRequest -Uri ($taskBase+'/auth/register') -Method POST -ContentType 'application/json; charset=utf-8' -Body ($taskRegistration | ConvertTo-Json -Compress) -SkipHttpErrorCheck -TimeoutSec 45
    if ([int]$taskResponse.StatusCode -eq 409) {
        $taskResponse = Invoke-WebRequest -Uri ($taskBase+'/auth/login') -Method POST -ContentType 'application/json; charset=utf-8' -Body ($taskCredentials | ConvertTo-Json -Compress) -SkipHttpErrorCheck -TimeoutSec 45
    }
    if ([int]$taskResponse.StatusCode -ne 200) { throw 'Registration/login failed' }
    $taskSession = $taskResponse.Content | ConvertFrom-Json
    if ($taskSetup.grant -eq $true) {
        $taskGrant = Invoke-WebRequest -Uri ($taskBase+'/internal/admin/grant') -Method POST -Headers @{Authorization=('Bearer '+$taskSetup.setup_token)} -ContentType 'application/json; charset=utf-8' -Body (@{email=$taskSetup.email}|ConvertTo-Json -Compress) -SkipHttpErrorCheck -TimeoutSec 45
        if ([int]$taskGrant.StatusCode -notin @(200,409)) { throw 'Grant failed' }
    }
    $taskMe = Invoke-RestMethod -Uri ($taskBase+'/me') -Headers @{Authorization=('Bearer '+$taskSession.token)} -TimeoutSec 45
    if ($taskSetup.grant -eq $true -and $taskMe.is_admin -ne $true) { throw 'Administrator verification failed' }
    if ($taskSetup.verify_personal -eq $true) {
        if ($taskMe.is_admin -ne $true) { throw 'Verification requires administrator authority' }
        function Invoke-CompanionApi($Route,$Method='GET',$Data=$null,$Token=$taskSession.token,$Expected=200) {
            $taskArguments=@{Uri=($taskBase+$Route);Method=$Method;Headers=@{Authorization=('Bearer '+$Token)};SkipHttpErrorCheck=$true;TimeoutSec=45}
            if ($null -ne $Data) { $taskArguments.Body=$Data|ConvertTo-Json -Depth 10 -Compress; $taskArguments.ContentType='application/json; charset=utf-8' }
            $taskReply=Invoke-WebRequest @taskArguments
            if ([int]$taskReply.StatusCode -ne $Expected) { throw ('Unexpected response on '+$Route) }
            if ($taskReply.Content) { $taskReply.Content|ConvertFrom-Json }
        }
        function Assert-Companion($Condition) { if (-not $Condition) { throw 'Live acceptance check failed' } }
        $taskFixtures=[Collections.Generic.List[object]]::new()
        try {
            for($taskN=0;$taskN -lt 2;$taskN++) {
                $taskEmail='qa-personal-'+[guid]::NewGuid().ToString('N')+'@example.com'
                $taskPassword=[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
                $taskFixture=Invoke-CompanionApi '/auth/register' 'POST' @{email=$taskEmail;password=$taskPassword;name='合成验收账号';timezone='Asia/Shanghai';consent_health_data=$true}
                $taskFixtures.Add($taskFixture)
            }
            $taskA=$taskFixtures[0];$taskB=$taskFixtures[1]
            $null=Invoke-CompanionApi '/admin/model-config' 'GET' $null $taskB.token 403
            $null=Invoke-CompanionApi '/admin/model-config' 'PUT' @{mode='disabled'} $taskB.token 403
            $taskConfig=Invoke-CompanionApi '/admin/model-config'
            Assert-Companion (-not ($taskConfig.PSObject.Properties.Name -contains 'api_key'))
            $taskMemory=Invoke-CompanionApi '/memories' 'POST' @{text='合成近期处境：这周在赶项目';category='context'} $taskA.token
            Assert-Companion ($taskMemory.is_active -and $taskMemory.expires_at)
            $taskExpired=Invoke-CompanionApi '/memories' 'POST' @{text='验收专用过期上下文XYZ';category='context';expires_at=[DateTime]::UtcNow.AddMinutes(-2).ToString('o')} $taskA.token
            Assert-Companion (-not $taskExpired.is_active)
            $taskPreferences=Invoke-CompanionApi '/preferences' 'PATCH' @{quiet_start='00:00';quiet_end='00:00';support_style='listen';life_followups_enabled=$true} $taskA.token
            Assert-Companion ($taskPreferences.support_style -eq 'listen')
            $taskThread=Invoke-CompanionApi '/threads' 'POST' @{title='合成验收面试';details='这是合成验收，不是真实生活事件';follow_up_enabled=$true;follow_up_at=[DateTime]::UtcNow.AddMinutes(-1).ToString('o')} $taskA.token
            $taskForeign=Invoke-CompanionApi '/threads' 'GET' $null $taskB.token
            Assert-Companion ($taskForeign.items.Count -eq 0)
            $null=Invoke-CompanionApi ('/threads/'+$taskThread.id) 'PATCH' @{status='completed'} $taskB.token 404
            $taskChat=Invoke-CompanionApi '/chat' 'POST' @{message='我这周一直在赶项目，下周一还要汇报，有点担心。'} $taskA.token
            Assert-Companion ($taskChat.suggestions.Count -eq 2)
            Assert-Companion (-not $taskChat.reply.Contains('验收专用过期上下文XYZ'))
            Assert-Companion ((Invoke-CompanionApi '/memories' 'GET' $null $taskA.token).items.Count -eq 2)
            $null=Invoke-CompanionApi '/evaluate' 'POST' @{} $taskA.token
            $taskReminders=(Invoke-CompanionApi '/reminders' 'GET' $null $taskA.token).items
            $taskLife=@($taskReminders|Where-Object {$_.category -eq 'life' -and $_.evidence_ids -contains $taskThread.id})
            Assert-Companion ($taskLife.Count -eq 1)
            $null=Invoke-CompanionApi ('/threads/'+$taskThread.id) 'PATCH' @{status='completed';outcome='合成验收已完成'} $taskA.token
            $taskCancelled=@((Invoke-CompanionApi '/reminders' 'GET' $null $taskA.token).items|Where-Object {$_.id -eq $taskLife[0].id})
            Assert-Companion ($taskCancelled[0].status -eq 'cancelled')
            $null=Invoke-CompanionApi ('/admin/users/'+$taskA.user.id+'/role') 'POST' @{is_admin=$true}
            $null=Invoke-CompanionApi '/admin/model-config' 'GET' $null $taskA.token
            $null=Invoke-CompanionApi ('/admin/users/'+$taskA.user.id+'/role') 'POST' @{is_admin=$false}
            $null=Invoke-CompanionApi '/admin/model-config' 'GET' $null $taskA.token 403
            $taskDirectory=Invoke-CompanionApi ('/admin/users?email='+[uri]::EscapeDataString($taskA.user.email))
            Assert-Companion ($taskDirectory.items.Count -eq 1 -and @($taskDirectory.items[0].PSObject.Properties).Count -eq 6)
        } finally {
            foreach($taskFixture in $taskFixtures) {
                $null=Invoke-CompanionApi ('/admin/users/'+$taskFixture.user.id+'/role') 'POST' @{is_admin=$false}
                $null=Invoke-CompanionApi '/me' 'DELETE' $null $taskFixture.token
            }
        }
    }
    $null=Invoke-RestMethod -Uri ($taskBase+'/auth/logout') -Method POST -Headers @{Authorization=('Bearer '+$taskSession.token)} -ContentType 'application/json' -Body '{}' -TimeoutSec 45
    @{registered=$true;administrator=($taskMe.is_admin -eq $true);email=$taskMe.email}|ConvertTo-Json -Compress
} catch {
    [Console]::Error.WriteLine('Account setup failed; credentials were not printed.')
    exit 1
}
