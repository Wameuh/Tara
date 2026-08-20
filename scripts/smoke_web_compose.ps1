$ErrorActionPreference = 'Stop'

function Invoke-DockerChecked {
  param([Parameter(Mandatory)][string[]]$Arguments)

  # Windows PowerShell promotes native stderr to an ErrorRecord when the global
  # preference is Stop. Docker uses stderr for normal pull/build progress, so
  # capture it under Continue and decide solely from the native exit code.
  $previousPreference = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $output = & docker @Arguments 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  if ($exitCode -ne 0) {
    $diagnostic = ($output | Select-Object -Last 20) -join "`n"
    throw "docker $($Arguments -join ' ') failed with exit $exitCode`n$diagnostic"
  }
  return ($output -join "`n")
}

function Wait-Http {
  param([Parameter(Mandatory)][string]$Path)

  $deadline = (Get-Date).AddSeconds(90)
  do {
    try {
      $response = Invoke-WebRequest "http://127.0.0.1:8080$Path" -UseBasicParsing
      if ($response.StatusCode -eq 200) {
        return
      }
    } catch {
      # The proxy or application can still be starting.
    }
    Start-Sleep -Seconds 2
  } while ((Get-Date) -lt $deadline)
  throw "Timed out waiting for $Path"
}

function Wait-ContainerStopped {
  param([Parameter(Mandatory)][string]$ContainerId)

  $deadline = (Get-Date).AddSeconds(60)
  do {
    $running = Invoke-DockerChecked -Arguments @(
      'inspect', '-f', '{{.State.Running}}', $ContainerId
    )
    if ($running.Trim() -eq 'false') {
      return
    }
    Start-Sleep -Seconds 1
  } while ((Get-Date) -lt $deadline)
  throw 'tara-web did not stop before the shutdown deadline'
}

$failure = $null
try {
  Invoke-DockerChecked -Arguments @('compose', 'config', '--quiet') | Out-Null
  Invoke-DockerChecked -Arguments @('compose', 'up', '--build', '-d') | Out-Null
  foreach ($path in @('/', '/api/v1/live', '/api/v1/ready')) {
    Wait-Http -Path $path
  }

  $id = Invoke-DockerChecked -Arguments @('compose', 'ps', '-q', 'tara-web')
  if (-not $id.Trim()) {
    throw 'tara-web container is missing'
  }
  $id = $id.Trim()

  $user = Invoke-DockerChecked -Arguments @('inspect', '-f', '{{.Config.User}}', $id)
  if ($user.Trim() -ne '10001:10001') {
    throw 'tara-web is not running with the expected non-root user'
  }
  $readOnly = Invoke-DockerChecked -Arguments @(
    'inspect', '-f', '{{.HostConfig.ReadonlyRootfs}}', $id
  )
  if ($readOnly.Trim() -ne 'true') {
    throw 'tara-web root filesystem is writable'
  }
  $ports = Invoke-DockerChecked -Arguments @(
    'inspect', '-f', '{{json .HostConfig.PortBindings}}', $id
  )
  if ($ports.Trim() -notin @('null', '{}')) {
    throw 'tara-web exposes a host port'
  }
  $capDrop = Invoke-DockerChecked -Arguments @(
    'inspect', '-f', '{{json .HostConfig.CapDrop}}', $id
  )
  if ($capDrop -notmatch 'ALL') {
    throw 'tara-web did not drop all Linux capabilities'
  }
  $securityOptions = Invoke-DockerChecked -Arguments @(
    'inspect', '-f', '{{json .HostConfig.SecurityOpt}}', $id
  )
  if ($securityOptions -notmatch 'no-new-privileges') {
    throw 'tara-web does not enforce no-new-privileges'
  }

  $writeSentinel = 'import sqlite3; c=sqlite3.connect("/data/runtime/tara-web.sqlite3"); c.execute("CREATE TABLE IF NOT EXISTS smoke_persistence (value TEXT NOT NULL)"); c.execute("INSERT INTO smoke_persistence VALUES (''sentinel'')"); c.commit()'
  $writeBytes = [Text.Encoding]::UTF8.GetBytes($writeSentinel) -join ','
  Invoke-DockerChecked -Arguments @(
    'compose', 'exec', '-T', 'tara-web', 'python', '-c',
    "exec(bytes([$writeBytes]))"
  ) | Out-Null
  $writeArtifact = @'
from datetime import UTC, datetime
import sqlite3
from tara_web.db.connection import ConnectionFactory
from tara_web.db.repositories.artifacts import ArtifactRepository
from tara_web.storage.artifacts import ArtifactService
from tara_web.storage.layout import StorageLayout
now = datetime.now(UTC).isoformat()
c = sqlite3.connect('/data/runtime/tara-web.sqlite3')
c.execute("INSERT OR IGNORE INTO upload_sessions(public_id,secret_hmac,status,expires_at,created_at,updated_at) VALUES ('smoke-session-0001', ?, 'consumed', ?, ?, ?)", ('v1:' + 'a' * 64, now, now, now))
c.execute("INSERT OR IGNORE INTO jobs(public_id,upload_session_id,secret_hmac,status,pipeline_version,expires_at,created_at,updated_at) VALUES ('smoke-job-00000001', 1, ?, 'queued', 'smoke', ?, ?, ?)", ('v1:' + 'a' * 64, now, now, now))
c.commit()
job_id = c.execute("SELECT id FROM jobs WHERE public_id='smoke-job-00000001'").fetchone()[0]
c.close()
factory = ConnectionFactory(__import__('pathlib').Path('/data/runtime/tara-web.sqlite3'), __import__('pathlib').Path('/data/runtime'))
outcome = ArtifactService(StorageLayout(__import__('pathlib').Path('/data/runtime')), ArtifactRepository(factory)).write(job_id=job_id, artifact_type='final_yaml', retention_kind='final_result', chunks=[b'smoke: persisted\n'])
assert outcome.error_code is None
'@
  $artifactBytes = [Text.Encoding]::UTF8.GetBytes($writeArtifact) -join ','
  Invoke-DockerChecked -Arguments @(
    'compose', 'exec', '-T', 'tara-web', 'python', '-c',
    "exec(bytes([$artifactBytes]))"
  ) | Out-Null
  Invoke-DockerChecked -Arguments @('compose', 'up', '-d', '--force-recreate', 'tara-web') | Out-Null
  Wait-Http -Path '/api/v1/ready'
  $id = (Invoke-DockerChecked -Arguments @('compose', 'ps', '-q', 'tara-web')).Trim()
  if (-not $id) {
    throw 'tara-web container is missing after recreation'
  }
  $checkSentinel = 'import sqlite3; c=sqlite3.connect("/data/runtime/tara-web.sqlite3"); assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"; assert c.execute("SELECT COUNT(*) FROM smoke_persistence WHERE value=''sentinel''").fetchone()[0] == 1; assert c.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] >= 5'
  $checkBytes = [Text.Encoding]::UTF8.GetBytes($checkSentinel) -join ','
  $persisted = Invoke-DockerChecked -Arguments @(
    'compose', 'exec', '-T', 'tara-web', 'python', '-c',
    "exec(bytes([$checkBytes]))"
  )
  $owner = Invoke-DockerChecked -Arguments @(
    'compose', 'exec', '-T', 'tara-web', 'sh', '-c', 'stat -c %u:%g /data/runtime/tara-web.sqlite3'
  )
  if ($owner.Trim() -ne '10001:10001') {
    throw 'SQLite volume owner changed during container recreation'
  }
  $checkArtifact = @'
import hashlib
import os
import sqlite3
c = sqlite3.connect('/data/runtime/tara-web.sqlite3')
relative_path, digest = c.execute("SELECT relative_path,sha256_hex FROM job_artifacts WHERE artifact_type='final_yaml' AND storage_state='ready' ORDER BY id DESC LIMIT 1").fetchone()
content = open('/data/runtime/' + relative_path, 'rb').read()
assert content == b'smoke: persisted\n'
assert hashlib.sha256(content).hexdigest() == digest
assert os.stat('/data/runtime/' + relative_path).st_uid == 10001
'@
  $artifactCheckBytes = [Text.Encoding]::UTF8.GetBytes($checkArtifact) -join ','
  Invoke-DockerChecked -Arguments @(
    'compose', 'exec', '-T', 'tara-web', 'python', '-c',
    "exec(bytes([$artifactCheckBytes]))"
  ) | Out-Null

  Invoke-DockerChecked -Arguments @(
    'compose', 'kill', '-s', 'SIGTERM', 'tara-web'
  ) | Out-Null
  Wait-ContainerStopped -ContainerId $id
  $exitCode = Invoke-DockerChecked -Arguments @(
    'inspect', '-f', '{{.State.ExitCode}}', $id
  )
  # Docker's init can report 128 + SIGTERM even when it forwarded the signal
  # and the application stopped before the grace deadline.
  if ([int]$exitCode.Trim() -notin @(0, 143)) {
    throw "tara-web did not exit gracefully: exit code $($exitCode.Trim())"
  }
} catch {
  $failure = $_
  throw
} finally {
  try {
    Invoke-DockerChecked -Arguments @(
      'compose', 'down', '-v', '--remove-orphans'
    ) | Out-Null
  } catch {
    if (-not $failure) {
      throw
    }
  }
}
