import { randomUUID } from 'node:crypto'
import type { AdminAccount } from './pi-api.js'

export type WebSession = {
  piToken: string
  admin: AdminAccount
  expiresAt: string
}

export class MemorySessionStore {
  private readonly sessions = new Map<string, WebSession>()

  create(session: WebSession): string {
    const id = randomUUID()
    this.sessions.set(id, session)
    return id
  }

  get(id: string | undefined): WebSession | undefined {
    return id ? this.sessions.get(id) : undefined
  }

  delete(id: string | undefined): void {
    if (id) {
      this.sessions.delete(id)
    }
  }
}
