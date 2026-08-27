import request from 'supertest'
import { describe, expect, it } from 'vitest'
import { createWebApp } from './app.js'
import { PiApiError } from './pi-api.js'
import type { PiApi } from './pi-api.js'

let testBaseUrl = 'http://127.0.0.1:8000'
let testHealthError: unknown
let testAdminEndpointError: { status: number; code: string; message: string } | undefined

const piApi: PiApi & { probeAdminLogin(): Promise<void> } = {
  async login(username: string, password: string) {
    if (username !== 'admin' || password !== 'AdminPass123!') {
      throw { status: 401, code: 'ADMIN_CREDENTIALS_INVALID' }
    }
    return {
      token: 'pi-token-123',
      expires_at: '2026-08-18 20:00:00',
      admin: { id: 1, username: 'admin', account_type: 'super_admin', enabled: 1 },
    }
  },
  async logout(token: string) {
    expect(token).toBe('pi-token-123')
  },
  async getMe(token: string) {
    expect(token).toBe('pi-token-123')
    return { id: 1, username: 'admin', account_type: 'super_admin', enabled: 1 }
  },
  async health() {
    if (testHealthError) throw testHealthError
    return { ok: true, msg: '慧签后端运行中' }
  },
  async probeAdminLogin() {
    if (testAdminEndpointError) throw new PiApiError(testAdminEndpointError.status, testAdminEndpointError.code, testAdminEndpointError.message)
  },
  getBaseUrl() {
    return testBaseUrl
  },
  setBaseUrl(baseUrl: string) {
    testBaseUrl = baseUrl
  },
  async getStudents(token: string) {
    expect(token).toBe('pi-token-123')
    return [{ id: 7, name: '刘恩泽', face_count: 3, fingerprint_count: 1 }]
  },
  async updateStudent(token: string, studentId: number, name: string, role: number) {
    expect(token).toBe('pi-token-123')
    expect(studentId).toBe(7)
    expect(name).toBe('梁健')
    expect(role).toBe(1)
  },
  async getRecords(token: string) {
    expect(token).toBe('pi-token-123')
    return [{ id: 11, user_id: 7, name: '刘恩泽', punch_time: '2026-08-18 09:00:00', kind: 'in' as const }]
  },
  async getAttendancePhoto(token: string, filename: string) {
    expect(token).toBe('pi-token-123')
    expect(filename).toBe('20260826_090000_000001.jpg')
    return { bytes: Buffer.from([0xff, 0xd8, 0xff]), contentType: 'image/jpeg' }
  },
  async getStats(token: string) {
    expect(token).toBe('pi-token-123')
    return { week_start: '2026-08-17', users: [{ user_id: 7, name: '刘恩泽', punches: 2, seconds: 3600 }] }
  },
  async getPresence(token: string) {
    expect(token).toBe('pi-token-123')
    return { count: 1, users: [{ user_id: 7, name: '刘恩泽', in_time: '2026-08-18 09:00:00' }] }
  },
  async getActivity(token: string) {
    expect(token).toBe('pi-token-123')
    return [{ id: 1, actor: 'admin:admin', action: 'administrator login', detail: 'session issued', created_at: '2026-08-18 09:00:00' }]
  },
  async getSettings(token: string) {
    expect(token).toBe('pi-token-123')
    return { punch_mode: 'window' as const, windows: [['07:30', '10:00']], out_deadline: '23:00' }
  },
  async setSettings(token: string) {
    expect(token).toBe('pi-token-123')
  },
  async listAdmins(token: string) {
    expect(token).toBe('pi-token-123')
    return [{ id: 1, username: 'admin', account_type: 'super_admin' as const, enabled: 1 }]
  },
  async createAdmin(token: string, username: string) {
    expect(token).toBe('pi-token-123')
    return { id: 2, username, account_type: 'admin' as const, enabled: 1 }
  },
  async updateAdminStatus(token: string, adminId: number, enabled: boolean) {
    expect(token).toBe('pi-token-123')
    return { id: adminId, username: 'monitor', account_type: 'admin' as const, enabled: Number(enabled) }
  },
  async resetAdminPassword(token: string, adminId: number) {
    expect(token).toBe('pi-token-123')
    return { id: adminId, username: 'monitor', account_type: 'admin' as const, enabled: 1 }
  },
  async changeOwnPassword(token: string) {
    expect(token).toBe('pi-token-123')
    return { id: 1, username: 'admin', account_type: 'super_admin' as const, enabled: 1 }
  },
}

describe('本机网页服务', () => {
  it('登录后使用 HttpOnly Cookie 保存会话，浏览器拿不到树莓派令牌', async () => {
    const app = createWebApp({ piApi, sessionSecret: 'test-secret' })
    const client = request.agent(app)

    const login = await client.post('/web-api/auth/login').send({
      username: 'admin',
      password: 'AdminPass123!',
    })
    const current = await client.get('/web-api/auth/me')

    expect(login.status).toBe(200)
    expect(login.headers['set-cookie'][0]).toContain('HttpOnly')
    expect(login.body).not.toHaveProperty('token')
    expect(current.status).toBe(200)
    expect(current.body.admin.username).toBe('admin')
  })

  it('退出登录后删除网页会话', async () => {
    const app = createWebApp({ piApi, sessionSecret: 'test-secret' })
    const client = request.agent(app)
    await client.post('/web-api/auth/login').send({ username: 'admin', password: 'AdminPass123!' })

    const logout = await client.post('/web-api/auth/logout')
    const current = await client.get('/web-api/auth/me')

    expect(logout.status).toBe(200)
    expect(current.status).toBe(401)
    expect(current.body.code).toBe('WEB_SESSION_REQUIRED')
  })

  it('将树莓派健康状态和受保护的学生数据安全转发给已登录浏览器', async () => {
    const app = createWebApp({ piApi, sessionSecret: 'test-secret' })
    const client = request.agent(app)
    await client.post('/web-api/auth/login').send({ username: 'admin', password: 'AdminPass123!' })

    const health = await client.get('/web-api/connection')
    const students = await client.get('/web-api/students')

    expect(health.status).toBe(200)
    expect(health.body.online).toBe(true)
    expect(students.status).toBe(200)
    expect(students.body.students[0].name).toBe('刘恩泽')
  })

  it('将学生姓名和角色修改安全转发给树莓派', async () => {
    const app = createWebApp({ piApi, sessionSecret: 'test-secret' })
    const client = request.agent(app)
    await client.post('/web-api/auth/login').send({ username: 'admin', password: 'AdminPass123!' })

    const updated = await client.post('/web-api/students/7').send({ name: '梁健', role: 1 })
    const invalid = await client.post('/web-api/students/7').send({ name: '', role: 3 })

    expect(updated.status).toBe(200)
    expect(updated.body).toEqual({ ok: true })
    expect(invalid.status).toBe(400)
    expect(invalid.body.code).toBe('INVALID_STUDENT_UPDATE')
  })

  it('将考勤、统计、设置和管理员操作经受保护的本机接口转发', async () => {
    const app = createWebApp({ piApi, sessionSecret: 'test-secret' })
    const client = request.agent(app)
    await client.post('/web-api/auth/login').send({ username: 'admin', password: 'AdminPass123!' })

    const attendance = await client.get('/web-api/attendance?date=2026-08-18')
    const statistics = await client.get('/web-api/statistics')
    const presence = await client.get('/web-api/presence')
    const activity = await client.get('/web-api/activity')
    const settings = await client.get('/web-api/settings')
    const admins = await client.get('/web-api/admins')
    const created = await client.post('/web-api/admins').send({ username: 'monitor', password: 'AdminPass123!' })

    expect(attendance.body.records[0].name).toBe('刘恩泽')
    expect(statistics.body.stats.users[0].punches).toBe(2)
    expect(presence.body.count).toBe(1)
    expect(activity.body.logs[0].actor).toBe('admin:admin')
    expect(settings.body.punch_mode).toBe('window')
    expect(admins.body.admins[0].account_type).toBe('super_admin')
    expect(created.status).toBe(201)
  })

  it('仅向已登录会话转发打卡照片', async () => {
    const app = createWebApp({ piApi, sessionSecret: 'test-secret' })
    const anonymous = await request(app).get('/web-api/attendance/photos/20260826_090000_000001.jpg')
    const client = request.agent(app)
    await client.post('/web-api/auth/login').send({ username: 'admin', password: 'AdminPass123!' })
    const photo = await client
      .get('/web-api/attendance/photos/20260826_090000_000001.jpg')
      .buffer(true)
      .parse((response, callback) => {
        const chunks: Buffer[] = []
        response.on('data', (chunk: Buffer) => chunks.push(chunk))
        response.on('end', () => callback(null, Buffer.concat(chunks)))
      })

    expect(anonymous.status).toBe(401)
    expect(photo.status).toBe(200)
    expect(photo.headers['content-type']).toContain('image/jpeg')
    expect(photo.body).toEqual(Buffer.from([0xff, 0xd8, 0xff]))
  })

  it('拒绝不安全的打卡照片文件名', async () => {
    const app = createWebApp({ piApi, sessionSecret: 'test-secret' })
    const client = request.agent(app)
    await client.post('/web-api/auth/login').send({ username: 'admin', password: 'AdminPass123!' })

    const invalid = await client.get('/web-api/attendance/photos/..%2Fhuiqian.db')

    expect(invalid.status).toBe(400)
    expect(invalid.body.code).toBe('INVALID_PHOTO_FILENAME')
  })

  it('保留树莓派打卡照片获取失败的状态码', async () => {
    const failingPiApi: PiApi = {
      ...piApi,
      async getAttendancePhoto() {
        throw new PiApiError(404, 'PHOTO_NOT_FOUND', 'photo not found')
      },
    }
    const app = createWebApp({ piApi: failingPiApi, sessionSecret: 'test-secret' })
    const client = request.agent(app)
    await client.post('/web-api/auth/login').send({ username: 'admin', password: 'AdminPass123!' })

    const missing = await client.get('/web-api/attendance/photos/20260826_090000_000001.jpg')

    expect(missing.status).toBe(404)
    expect(missing.body.code).toBe('PHOTO_NOT_FOUND')
  })

  it('切换后端地址时先探活，成功返回新地址，失败恢复旧地址', async () => {
    testBaseUrl = 'http://127.0.0.1:8000'
    testHealthError = undefined
    const app = createWebApp({ piApi, sessionSecret: 'test-secret' })
    const client = request.agent(app)
    await client.post('/web-api/auth/login').send({ username: 'admin', password: 'AdminPass123!' })

    const switched = await client.post('/web-api/connection').send({ base_url: 'http://10.42.0.1:8000' })
    expect(switched.status).toBe(200)
    expect(switched.body).toMatchObject({ ok: true, online: true, base_url: 'http://10.42.0.1:8000' })
    expect(testBaseUrl).toBe('http://10.42.0.1:8000')

    testHealthError = { status: 503, code: 'PI_UNREACHABLE', message: 'offline' }
    const failed = await client.post('/web-api/connection').send({ base_url: 'http://10.42.0.2:8000' })
    expect(failed.status).toBe(503)
    expect(failed.body.code).toBe('PI_UNREACHABLE')
    expect(testBaseUrl).toBe('http://10.42.0.1:8000')
    testHealthError = undefined
  })

  it('切换失败后保留最近一次健康地址，并返回当前离线地址', async () => {
    testBaseUrl = 'http://127.0.0.1:8000'
    testHealthError = undefined
    const app = createWebApp({ piApi, sessionSecret: 'test-secret' })
    const client = request.agent(app)
    await client.get('/web-api/connection')
    await client.post('/web-api/auth/login').send({ username: 'admin', password: 'AdminPass123!' })

    testHealthError = { status: 503, code: 'PI_UNREACHABLE', message: 'offline' }
    const failed = await client.post('/web-api/connection').send({ base_url: 'http://10.42.0.1:8000' })
    const current = await client.get('/web-api/connection')

    expect(failed.status).toBe(503)
    expect(testBaseUrl).toBe('http://127.0.0.1:8000')
    expect(current.status).toBe(200)
    expect(current.body).toMatchObject({ ok: false, online: false, base_url: 'http://127.0.0.1:8000' })
    testHealthError = undefined
  })

  it('树莓派没有管理员登录接口时拒绝切换并回退本机', async () => {
    testBaseUrl = 'http://127.0.0.1:8000'
    testHealthError = undefined
    testAdminEndpointError = undefined
    const app = createWebApp({ piApi, sessionSecret: 'test-secret' })
    const client = request.agent(app)
    await client.get('/web-api/connection')
    await client.post('/web-api/auth/login').send({ username: 'admin', password: 'AdminPass123!' })

    testAdminEndpointError = { status: 503, code: 'PI_LOGIN_ENDPOINT_MISSING', message: 'login endpoint missing' }
    const switched = await client.post('/web-api/connection').send({ base_url: 'http://10.42.0.1:8000' })

    expect(switched.status).toBe(503)
    expect(switched.body.code).toBe('PI_LOGIN_ENDPOINT_MISSING')
    expect(testBaseUrl).toBe('http://127.0.0.1:8000')
    testAdminEndpointError = undefined
  })
})
