import { execSync } from 'node:child_process'
import { useEffect, useState } from 'react'

export interface GitStatus {
  branch: string
  dirty: boolean
}

const EMPTY: GitStatus = { branch: '', dirty: false }

// Poll git status every 5s from cwd. Returns empty when not inside a repo.
// Uses synchronous exec to avoid async lifecycle complexity inside React;
// the poll interval is long enough that the blocking cost is negligible.
function readGitStatus(cwd: string): GitStatus {
  try {
    const branch = execSync('git rev-parse --abbrev-ref HEAD', {
      cwd,
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe'],
      timeout: 2000
    })
      .toString()
      .trim()

    if (!branch) {
      return EMPTY
    }

    // porcelain = stable parseable format; --quiet suppresses "nothing to commit"
    const status = execSync('git status --porcelain', {
      cwd,
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe'],
      timeout: 2000
    })
      .toString()
      .trim()

    return { branch, dirty: status.length > 0 }
  } catch {
    return EMPTY
  }
}

export function useGitStatus(cwd: string, enabled = true): GitStatus {
  const [status, setStatus] = useState<GitStatus>(EMPTY)

  useEffect(() => {
    if (!enabled || !cwd) {
      return
    }

    // Initial read
    setStatus(readGitStatus(cwd))

    const id = setInterval(() => {
      setStatus(readGitStatus(cwd))
    }, 5000)

    return () => clearInterval(id)
  }, [cwd, enabled])

  return status
}
