import cookieParser from 'cookie-parser'
import express, { type NextFunction, type Request, type Response } from 'express'
import { PiApiError, type PiApi } from './pi-api.js'
import { MemorySessionStore, type WebSession } from './session-store.js'

const SESSION_COOKIE = 'huiqian_session'

type WebAppOptions = {
  piApi: PiApi
  sessionSecret: string
  sessionStore?: MemorySessionStore
}

function writeSessionCookie(response: Response, sessionId: string): void {
  response.cookie(SESSION_COOKIE, sessionId, {
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    signed: true,
    path: '/',
    maxAge: 12 * 60 * 60 * 1_000,
  })
}

function clearSessionCookie(response: Response): void {
  response.clearCookie(SESSION_COOKIE, { httpOnly: true, sameSite: 'lax', signed: true, path: '/' })
}

function mapPiError(error: unknown, response: Response): void {
  if (error instanceof PiApiError) {
    response.status(error.status).json({ ok: false, code: error.code, msg: error.message })
    return
  }
  response.status(503).json({ ok: false, code: 'PI_UNREACHABLE', msg: '树莓派后端无法连接' })
}

export function createWebApp({ piApi, sessionSecret, sessionStore = new MemorySessionStore() }: WebAppOptions) {
  const app = express()
  let lastHealthyBaseUrl = piApi.getBaseUrl()
  app.disable('x-powered-by')
  app.use(express.json({ limit: '32kb' }))
  app.use(cookieParser(sessionSecret))

  function readSession(request: Request): WebSession | undefined {
    return sessionStore.get(request.signedCookies?.[SESSION_COOKIE])
  }

  function requireSession(request: Request, response: Response, next: NextFunction): void {
    const session = readSession(request)
    if (!session) {
      response.status(401).json({ ok: false, code: 'WEB_SESSION_REQUIRED', msg: '请先登录管理后台' })
      return
    }
    response.locals.webSession = session
    next()
  }

  app.post('/web-api/auth/login', async (request, response) => {
    const username = String(request.body?.username ?? '').trim()
    const password = String(request.body?.password ?? '')
    if (!username || !password) {
      response.status(400).json({ ok: false, code: 'CREDENTIALS_REQUIRED', msg: '请输入用户名和密码' })
      return
    }
    try {
      const result = await piApi.login(username, password)
      const sessionId = sessionStore.create({
        piToken: result.token,
        admin: result.admin,
        expiresAt: result.expires_at,
      })
      writeSessionCookie(response, sessionId)
      response.status(200).json({ ok: true, admin: result.admin, expires_at: result.expires_at })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.post('/web-api/auth/logout', requireSession, async (request, response) => {
    const session = response.locals.webSession as WebSession
    const sessionId = request.signedCookies?.[SESSION_COOKIE]
    sessionStore.delete(sessionId)
    clearSessionCookie(response)
    try {
      await piApi.logout(session.piToken)
    } catch {
      // Local logout must still complete when the Pi is currently unavailable.
    }
    response.json({ ok: true })
  })

  app.get('/web-api/auth/me', requireSession, async (request, response) => {
    const session = response.locals.webSession as WebSession
    try {
      const admin = await piApi.getMe(session.piToken)
      session.admin = admin
      response.json({ ok: true, admin, expires_at: session.expiresAt })
    } catch (error) {
      if (error instanceof PiApiError && error.status === 401) {
        sessionStore.delete(request.signedCookies?.[SESSION_COOKIE])
        clearSessionCookie(response)
      }
      mapPiError(error, response)
    }
  })

  app.get('/web-api/connection', async (_request, response) => {
    try {
      const health = await piApi.health()
      if (health.ok) lastHealthyBaseUrl = piApi.getBaseUrl()
      response.json({ ok: true, online: Boolean(health.ok), message: health.msg, base_url: piApi.getBaseUrl(), checked_at: new Date().toISOString() })
    } catch (error) {
      response.status(200).json({
        ok: false,
        online: false,
        code: error instanceof PiApiError ? error.code : 'PI_UNREACHABLE',
        message: error instanceof Error ? error.message : '树莓派后端无法连接',
        base_url: piApi.getBaseUrl(),
        checked_at: new Date().toISOString(),
      })
    }
  })

  app.post('/web-api/connection', requireSession, async (request, response) => {
    const requestedBaseUrl = String(request.body?.base_url ?? '')
    try {
      piApi.setBaseUrl(requestedBaseUrl)
      const health = await piApi.health()
      if (!health.ok) {
        throw new PiApiError(503, 'PI_UNREACHABLE', health.msg || '无法连接新的后端地址')
      }
      await piApi.probeAdminLogin()
      lastHealthyBaseUrl = piApi.getBaseUrl()
      response.json({ ok: true, online: true, message: health.msg, base_url: piApi.getBaseUrl(), checked_at: new Date().toISOString() })
    } catch (error) {
      try {
        piApi.setBaseUrl(lastHealthyBaseUrl)
      } catch {
        // Keep the original error when the configured address cannot be restored.
      }
      mapPiError(error, response)
    }
  })

  app.get('/web-api/students', requireSession, async (_request, response) => {
    const session = response.locals.webSession as WebSession
    try {
      const students = await piApi.getStudents(session.piToken)
      response.json({ ok: true, students })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.post('/web-api/students/:id', requireSession, async (request, response) => {
    const session = response.locals.webSession as WebSession
    const studentId = Number(request.params.id)
    const name = String(request.body?.name ?? '').trim()
    const role = Number(request.body?.role)
    if (!Number.isInteger(studentId) || studentId < 1 || !name || ![0, 1].includes(role)) {
      response.status(400).json({ ok: false, code: 'INVALID_STUDENT_UPDATE', msg: '姓名和角色参数无效' })
      return
    }
    try {
      await piApi.updateStudent(session.piToken, studentId, name, role)
      response.json({ ok: true })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.get('/web-api/attendance', requireSession, async (request, response) => {
    const session = response.locals.webSession as WebSession
    const date = typeof request.query.date === 'string' ? request.query.date : undefined
    const userId = typeof request.query.user_id === 'string' ? Number(request.query.user_id) : undefined
    try {
      const records = await piApi.getRecords(session.piToken, {
        ...(date ? { date } : {}),
        ...(Number.isInteger(userId) ? { user_id: userId } : {}),
      })
      response.json({ ok: true, records })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.get('/web-api/attendance/photos/:filename', requireSession, async (request, response) => {
    const filename = String(request.params.filename ?? '')
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(filename)) {
      response.status(400).json({ ok: false, code: 'INVALID_PHOTO_FILENAME', msg: '照片文件名无效' })
      return
    }
    const session = response.locals.webSession as WebSession
    try {
      const photo = await piApi.getAttendancePhoto(session.piToken, filename)
      if (!photo.contentType.toLowerCase().startsWith('image/')) {
        throw new PiApiError(502, 'INVALID_PHOTO_CONTENT_TYPE', '树莓派返回的不是图片')
      }
      response.type(photo.contentType).send(photo.bytes)
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.get('/web-api/statistics', requireSession, async (request, response) => {
    const session = response.locals.webSession as WebSession
    const weekStart = typeof request.query.week_start === 'string' ? request.query.week_start : undefined
    try {
      const stats = await piApi.getStats(session.piToken, weekStart)
      response.json({ ok: true, stats })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.get('/web-api/presence', requireSession, async (_request, response) => {
    const session = response.locals.webSession as WebSession
    try {
      const presence = await piApi.getPresence(session.piToken)
      response.json({ ok: true, ...presence })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.get('/web-api/activity', requireSession, async (_request, response) => {
    const session = response.locals.webSession as WebSession
    try {
      const logs = await piApi.getActivity(session.piToken)
      response.json({ ok: true, logs })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.get('/web-api/settings', requireSession, async (_request, response) => {
    const session = response.locals.webSession as WebSession
    try {
      const settings = await piApi.getSettings(session.piToken)
      response.json({ ok: true, ...settings })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.post('/web-api/settings', requireSession, async (request, response) => {
    const session = response.locals.webSession as WebSession
    try {
      await piApi.setSettings(session.piToken, request.body ?? {})
      response.json({ ok: true })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.post('/web-api/auth/password', requireSession, async (request, response) => {
    const session = response.locals.webSession as WebSession
    const sessionId = request.signedCookies?.[SESSION_COOKIE]
    try {
      const admin = await piApi.changeOwnPassword(
        session.piToken,
        String(request.body?.current_password ?? ''),
        String(request.body?.new_password ?? ''),
      )
      sessionStore.delete(sessionId)
      clearSessionCookie(response)
      response.json({ ok: true, admin })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.get('/web-api/admins', requireSession, async (_request, response) => {
    const session = response.locals.webSession as WebSession
    try {
      const admins = await piApi.listAdmins(session.piToken)
      response.json({ ok: true, admins })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.post('/web-api/admins', requireSession, async (request, response) => {
    const session = response.locals.webSession as WebSession
    try {
      const admin = await piApi.createAdmin(session.piToken, String(request.body?.username ?? ''), String(request.body?.password ?? ''))
      response.status(201).json({ ok: true, admin })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.post('/web-api/admins/:id/status', requireSession, async (request, response) => {
    const session = response.locals.webSession as WebSession
    try {
      const admin = await piApi.updateAdminStatus(session.piToken, Number(request.params.id), Boolean(request.body?.enabled))
      response.json({ ok: true, admin })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  app.post('/web-api/admins/:id/password', requireSession, async (request, response) => {
    const session = response.locals.webSession as WebSession
    try {
      const admin = await piApi.resetAdminPassword(session.piToken, Number(request.params.id), String(request.body?.password ?? ''))
      response.json({ ok: true, admin })
    } catch (error) {
      mapPiError(error, response)
    }
  })

  return app
}
