import axios, { type AxiosInstance } from 'axios'

export type AdminAccount = {
  id: number
  username: string
  account_type: 'super_admin' | 'admin'
  enabled: number
}

export type LoginResult = {
  token: string
  expires_at: string
  admin: AdminAccount
}

export type AttendanceRecord = {
  id: number
  user_id: number
  name: string
  punch_time: string
  kind: 'in' | 'out'
  photo?: string | null
}

export type AttendancePhoto = {
  bytes: Buffer
  contentType: string
}

export type PresenceUser = {
  user_id: number
  name: string
  in_time: string
}

export type ActivityLog = {
  id: number
  actor: string
  action: string
  detail: string
  created_at: string
}

export type PunchSettings = {
  punch_mode: 'unlimited' | 'window'
  windows: string[][]
  out_deadline: string
}

export type PiApi = {
  login(username: string, password: string): Promise<LoginResult>
  logout(token: string): Promise<void>
  getMe(token: string): Promise<AdminAccount>
  health(): Promise<{ ok: boolean; msg: string }>
  probeAdminLogin(): Promise<void>
  getBaseUrl(): string
  setBaseUrl(baseUrl: string): void
  getStudents(token: string): Promise<unknown[]>
  updateStudent(token: string, studentId: number, name: string, role: number): Promise<void>
  getRecords(token: string, params?: { date?: string; user_id?: number }): Promise<AttendanceRecord[]>
  getAttendancePhoto(token: string, filename: string): Promise<AttendancePhoto>
  getStats(token: string, weekStart?: string): Promise<unknown>
  getPresence(token: string): Promise<{ count: number; users: PresenceUser[] }>
  getActivity(token: string): Promise<ActivityLog[]>
  getSettings(token: string): Promise<PunchSettings>
  setSettings(token: string, settings: Partial<PunchSettings>): Promise<void>
  listAdmins(token: string): Promise<AdminAccount[]>
  createAdmin(token: string, username: string, password: string): Promise<AdminAccount>
  updateAdminStatus(token: string, adminId: number, enabled: boolean): Promise<AdminAccount>
  resetAdminPassword(token: string, adminId: number, password: string): Promise<AdminAccount>
  changeOwnPassword(token: string, currentPassword: string, newPassword: string): Promise<AdminAccount>
}

export class PiApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message?: string,
  ) {
    super(message ?? code)
  }
}

export class HttpPiApi implements PiApi {
  private client: AxiosInstance
  private baseUrl: string

  constructor(baseUrl: string) {
    this.baseUrl = normalizeBaseUrl(baseUrl)
    this.client = axios.create({ baseURL: this.baseUrl, timeout: 8_000 })
  }

  getBaseUrl(): string {
    return this.baseUrl
  }

  setBaseUrl(baseUrl: string): void {
    this.baseUrl = normalizeBaseUrl(baseUrl)
    this.client = axios.create({ baseURL: this.baseUrl, timeout: 8_000 })
  }

  async login(username: string, password: string): Promise<LoginResult> {
    const body = await this.request<{ token: string; expires_at: string; admin: AdminAccount }>(
      'post',
      '/api/admin/auth/login',
      undefined,
      { username, password },
    )
    return body
  }

  async logout(token: string): Promise<void> {
    await this.request('post', '/api/admin/auth/logout', token)
  }

  async getMe(token: string): Promise<AdminAccount> {
    const body = await this.request<{ admin: AdminAccount }>('get', '/api/admin/auth/me', token)
    return body.admin
  }

  async health(): Promise<{ ok: boolean; msg: string }> {
    return this.request('get', '/api/health')
  }

  async probeAdminLogin(): Promise<void> {
    try {
      await this.request('post', '/api/admin/auth/login', undefined, {})
    } catch (error) {
      if (error instanceof PiApiError && [400, 401, 422].includes(error.status)) return
      if (error instanceof PiApiError && [404, 405].includes(error.status)) {
        throw new PiApiError(503, 'PI_LOGIN_ENDPOINT_MISSING', '树莓派后端不支持管理员登录接口，请先更新后端代码')
      }
      throw error
    }
  }

  async getStudents(token: string): Promise<unknown[]> {
    const body = await this.request<{ users: unknown[] }>('get', '/api/users', token)
    return body.users
  }

  async updateStudent(token: string, studentId: number, name: string, role: number): Promise<void> {
    await this.request('post', '/api/rename', token, { user_id: studentId, name })
    await this.request('post', '/api/set_role', token, { user_id: studentId, role })
  }

  async getRecords(token: string, params?: { date?: string; user_id?: number }): Promise<AttendanceRecord[]> {
    const body = await this.request<{ records: AttendanceRecord[] }>('get', '/api/records', token, undefined, params)
    return body.records
  }

  async getAttendancePhoto(token: string, filename: string): Promise<AttendancePhoto> {
    try {
      const response = await this.client.get<ArrayBuffer>(`/static/photos/${encodeURIComponent(filename)}`, {
        headers: { Authorization: `Bearer ${token}` },
        responseType: 'arraybuffer',
      })
      return {
        bytes: Buffer.from(response.data),
        contentType: String(response.headers['content-type'] ?? 'application/octet-stream'),
      }
    } catch (error) {
      throw this.toPiApiError(error)
    }
  }

  async getStats(token: string, weekStart?: string): Promise<unknown> {
    const body = await this.request<{ stats: unknown }>('get', '/api/stats', token, undefined, weekStart ? { week_start: weekStart } : undefined)
    return body.stats
  }

  async getPresence(token: string): Promise<{ count: number; users: PresenceUser[] }> {
    return this.request('get', '/api/presence', token)
  }

  async getActivity(token: string): Promise<ActivityLog[]> {
    const body = await this.request<{ logs: ActivityLog[] }>('get', '/api/activity', token)
    return body.logs
  }

  async getSettings(token: string): Promise<PunchSettings> {
    return this.request('get', '/api/settings', token)
  }

  async setSettings(token: string, settings: Partial<PunchSettings>): Promise<void> {
    await this.request('post', '/api/settings', token, settings)
  }

  async listAdmins(token: string): Promise<AdminAccount[]> {
    const body = await this.request<{ admins: AdminAccount[] }>('get', '/api/admin/accounts', token)
    return body.admins
  }

  async createAdmin(token: string, username: string, password: string): Promise<AdminAccount> {
    const body = await this.request<{ admin: AdminAccount }>('post', '/api/admin/accounts', token, { username, password })
    return body.admin
  }

  async updateAdminStatus(token: string, adminId: number, enabled: boolean): Promise<AdminAccount> {
    const body = await this.request<{ admin: AdminAccount }>('post', `/api/admin/accounts/${adminId}/status`, token, { enabled })
    return body.admin
  }

  async resetAdminPassword(token: string, adminId: number, password: string): Promise<AdminAccount> {
    const body = await this.request<{ admin: AdminAccount }>('post', `/api/admin/accounts/${adminId}/password`, token, { password })
    return body.admin
  }

  async changeOwnPassword(token: string, currentPassword: string, newPassword: string): Promise<AdminAccount> {
    const body = await this.request<{ admin: AdminAccount }>('post', '/api/admin/auth/password', token, { current_password: currentPassword, new_password: newPassword })
    return body.admin
  }

  private async request<T>(
    method: 'get' | 'post',
    url: string,
    token?: string,
    data?: unknown,
    params?: Record<string, string | number>,
  ): Promise<T> {
    try {
      const response = await this.client.request<T>({
        method,
        url,
        data,
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        params,
      })
      return response.data
    } catch (error) {
      throw this.toPiApiError(error)
    }
  }

  private toPiApiError(error: unknown): unknown {
    if (axios.isAxiosError(error)) {
      const payload = error.response?.data as { code?: string; msg?: string } | undefined
      return new PiApiError(
        error.response?.status ?? 503,
        payload?.code ?? 'PI_UNREACHABLE',
        payload?.msg ?? error.message,
      )
    }
    return error
  }
}

export function normalizeBaseUrl(value: string): string {
  const input = value.trim()
  let parsed: URL
  try {
    parsed = new URL(input)
  } catch {
    throw new PiApiError(400, 'INVALID_PI_BASE_URL', '后端地址格式不正确')
  }
  if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname || !parsed.port || parsed.username || parsed.password || parsed.pathname !== '/' || parsed.search || parsed.hash) {
    throw new PiApiError(400, 'INVALID_PI_BASE_URL', '请输入带端口的 HTTP 后端地址，例如 http://192.168.1.20:8000')
  }
  const port = Number(parsed.port)
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new PiApiError(400, 'INVALID_PI_BASE_URL', '后端端口必须是 1 到 65535')
  }
  return parsed.origin
}
