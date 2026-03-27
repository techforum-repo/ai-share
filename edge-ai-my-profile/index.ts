/* eslint-disable max-lines */
// ─────────────────────────────────────────────────────────────────────────────
// Albin Issac Portfolio — AI Worker
// Routes:
//   POST /api/ai/chat          — RAG-powered chat about Albin
//   POST /api/ai/contact       — Smart contact form analysis
//   POST /api/ai/visitor       — Dynamic visitor personalization
//   POST /api/ai/analyze       — Page content analyzer
//   POST /api/ai/index         — Index portfolio content into Vectorize (admin)
// ─────────────────────────────────────────────────────────────────────────────

import { PORTFOLIO_CHUNKS } from './content'

interface Env {
  AI: Ai
  VECTORIZE: VectorizeIndex
  ALLOWED_ORIGIN: string
  AI_GATEWAY_ID: string
  INDEX_SECRET: string
}

const MODEL = '@cf/meta/llama-3.1-8b-instruct'
const EMBED_MODEL = '@cf/baai/bge-base-en-v1.5'

// ── Base system prompt (RAG context is appended dynamically) ──────────────────
const SYSTEM_PROMPT = `You are an AI assistant embedded in Albin Issac's personal portfolio website.
Albin Issac is an AI-Enabled MarTech Architect with 20+ years of experience.
Answer questions about Albin using the provided context. Be concise and professional.
If context is provided below, use it to answer accurately.
If asked about topics unrelated to Albin or his expertise, politely redirect.
Never make up facts about Albin beyond what is provided.`

// ── Gateway options per route ─────────────────────────────────────────────────
function gatewayOptions(id: string, skipCache: boolean, cacheTtl = 3600) {
  return { gateway: { id, skipCache, cacheTtl } }
}

// ── Request validation ────────────────────────────────────────────────────────
function isAllowedSource(request: Request, allowedOrigin: string): boolean {
  const originHeader = request.headers.get('Origin')
  const refererHeader = request.headers.get('Referer')
  const isLocalhost = (s: string) =>
    s.startsWith('http://localhost') || s.startsWith('http://127.0.0.1')
  if (originHeader) return originHeader === allowedOrigin || isLocalhost(originHeader)
  if (refererHeader) return refererHeader.startsWith(allowedOrigin) || isLocalhost(refererHeader)
  return false
}

// ── CORS helpers ──────────────────────────────────────────────────────────────
function corsHeaders(origin: string): Record<string, string> {
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json'
  }
}

function optionsResponse(origin: string): Response {
  return new Response(null, { status: 204, headers: corsHeaders(origin) })
}

function jsonResponse(data: unknown, origin: string, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: corsHeaders(origin) })
}

function errorResponse(message: string, origin: string, status = 500): Response {
  return jsonResponse({ error: message }, origin, status)
}

// ── RAG helpers ───────────────────────────────────────────────────────────────
async function embedText(text: string, env: Env): Promise<number[]> {
  const result = (await env.AI.run(EMBED_MODEL, { text: [text] })) as { data: number[][] }
  return result.data[0]
}

// Embed query, search Vectorize, return top matching text chunks as context string
async function retrieveContext(query: string, env: Env): Promise<string> {
  const queryEmbedding = await embedText(query, env)
  const results = await env.VECTORIZE.query(queryEmbedding, { topK: 4, returnMetadata: 'all' })

  if (!results.matches?.length) return ''

  return results.matches
    .filter(m => (m.score ?? 0) > 0.55)
    .map(m => (m.metadata as Record<string, string>)?.text ?? '')
    .filter(Boolean)
    .join('\n\n')
}

// ── Route: POST /api/ai/chat ──────────────────────────────────────────────────
// Body: { message: string, history?: { role: 'user'|'assistant', content: string }[] }
// Returns: { response: string }
async function handleChat(
  body: Record<string, unknown>,
  env: Env,
  origin: string
): Promise<Response> {
  const message = typeof body.message === 'string' ? body.message.trim() : ''
  if (!message) return errorResponse('message is required', origin, 400)
  if (message.length > 500) return errorResponse('message too long (max 500 chars)', origin, 400)

  const rawHistory = Array.isArray(body.history) ? body.history : []
  const history: RoleScopedChatInput[] = rawHistory
    .filter(
      (h): h is { role: string; content: string } =>
        typeof h === 'object' &&
        h !== null &&
        (h.role === 'user' || h.role === 'assistant') &&
        typeof h.content === 'string'
    )
    .slice(-10)
    .map(h => ({ role: h.role as 'user' | 'assistant', content: h.content.slice(0, 500) }))

  // RAG: retrieve relevant context from Vectorize
  const context = await retrieveContext(message, env)
  const systemContent = context
    ? `${SYSTEM_PROMPT}\n\nRelevant context from Albin's profile:\n${context}`
    : SYSTEM_PROMPT

  const messages: RoleScopedChatInput[] = [
    { role: 'system', content: systemContent },
    ...history,
    { role: 'user', content: message }
  ]

  const result = (await env.AI.run(
    MODEL,
    { messages },
    gatewayOptions(env.AI_GATEWAY_ID, true)
  )) as { response: string }

  return jsonResponse({ response: result.response ?? '' }, origin)
}

// ── Route: POST /api/ai/index ─────────────────────────────────────────────────
// Protected by Authorization: Bearer <INDEX_SECRET>
// Embeds all PORTFOLIO_CHUNKS and upserts into Vectorize.
// Call once after deploy, and again whenever portfolio content changes.
async function handleIndex(request: Request, env: Env, origin: string): Promise<Response> {
  const auth = request.headers.get('Authorization') ?? ''
  if (auth !== `Bearer ${env.INDEX_SECRET}`) {
    return errorResponse('Unauthorized', origin, 401)
  }

  const vectors: VectorizeVector[] = []

  for (const chunk of PORTFOLIO_CHUNKS) {
    const embedding = await embedText(chunk.text, env)
    vectors.push({
      id: chunk.id,
      values: embedding,
      metadata: { text: chunk.text }
    })
  }

  await env.VECTORIZE.upsert(vectors)

  return jsonResponse({ indexed: vectors.length }, origin)
}

// ── Route: POST /api/ai/contact ───────────────────────────────────────────────
// Body: { name: string, email: string, message: string }
// Returns: { category, priority, summary, suggestedReply }
async function handleContact(
  body: Record<string, unknown>,
  env: Env,
  origin: string
): Promise<Response> {
  const name = typeof body.name === 'string' ? body.name.trim() : ''
  const email = typeof body.email === 'string' ? body.email.trim() : ''
  const message = typeof body.message === 'string' ? body.message.trim() : ''

  if (!name || !email || !message) {
    return errorResponse('name, email and message are required', origin, 400)
  }

  const truncatedMessage = message.slice(0, 1000)

  const prompt = `Analyze this contact form submission sent to Albin Issac (MarTech Architect) and respond ONLY with valid JSON — no markdown, no explanation:

{
  "category": "job-offer" | "freelance" | "collaboration" | "feedback" | "speaking" | "other",
  "priority": "high" | "medium" | "low",
  "summary": "<one sentence summary of what the person wants>",
  "suggestedReply": "<a short, warm, professional reply Albin could send>"
}

Name: ${name}
Email: ${email}
Message: ${truncatedMessage}`

  const messages: RoleScopedChatInput[] = [
    {
      role: 'system',
      content:
        'You are a helpful assistant. Respond ONLY with valid JSON, no markdown or extra text.'
    },
    { role: 'user', content: prompt }
  ]

  const result = (await env.AI.run(
    MODEL,
    { messages },
    gatewayOptions(env.AI_GATEWAY_ID, true)
  )) as { response: string }

  try {
    const clean = result.response.replace(/```json|```/g, '').trim()
    return jsonResponse(JSON.parse(clean), origin)
  } catch {
    return jsonResponse({ raw: result.response }, origin)
  }
}

// ── Route: POST /api/ai/visitor ───────────────────────────────────────────────
// Body: { referrer?: string, hint?: string }
// Returns: { type, headline, subheadline, cta, reason }
// Cache: 1 hour
async function handleVisitor(
  body: Record<string, unknown>,
  env: Env,
  origin: string
): Promise<Response> {
  const referrer = typeof body.referrer === 'string' ? body.referrer : 'direct'
  const hint = typeof body.hint === 'string' ? body.hint : ''

  const prompt = `A visitor arrived on Albin Issac's portfolio. Classify them and suggest personalised content.
Respond ONLY with valid JSON — no markdown:

{
  "type": "recruiter" | "developer" | "client" | "other",
  "reason": "<one sentence>",
  "headline": "<tailored hero headline, max 10 words>",
  "subheadline": "<tailored subheadline, max 20 words>",
  "ctaKey": "view-resume" | "discuss-project" | "read-blog" | "open-source" | "get-in-touch" | "explore-portfolio"
}

ctaKey guide:
- view-resume: recruiter wanting to see experience/CV
- discuss-project: client with a project in mind
- read-blog: knowledge seeker / general interest
- open-source: developer wanting to see code
- get-in-touch: general networking / unclear intent
- explore-portfolio: first-time or unknown visitor

Referrer: ${referrer}
Hint: ${hint || 'none'}

Guide: recruiter=LinkedIn/job boards, developer=GitHub/tech blogs, client=Google/company site, other=direct/unclear`

  const messages: RoleScopedChatInput[] = [
    {
      role: 'system',
      content:
        'You are a helpful assistant. Respond ONLY with valid JSON, no markdown or extra text.'
    },
    { role: 'user', content: prompt }
  ]

  const result = (await env.AI.run(
    MODEL,
    { messages },
    gatewayOptions(env.AI_GATEWAY_ID, false, 3600)
  )) as { response: string }

  try {
    const clean = result.response.replace(/```json|```/g, '').trim()
    return jsonResponse(JSON.parse(clean), origin)
  } catch {
    return jsonResponse(
      {
        type: 'other',
        reason: 'Could not classify visitor',
        headline: "Hi, I'm Albin Issac",
        subheadline: 'MarTech Architect & Full Stack Engineer with 20+ years of experience',
        ctaKey: 'explore-portfolio'
      },
      origin
    )
  }
}

// ── Route: POST /api/ai/analyze ───────────────────────────────────────────────
// Body: { section: string, content: string }
// Returns: { suggestions: { issue, fix, impact }[] }
// Cache: 24 hours
async function handleAnalyze(
  body: Record<string, unknown>,
  env: Env,
  origin: string
): Promise<Response> {
  const section = typeof body.section === 'string' ? body.section.trim() : ''
  const content = typeof body.content === 'string' ? body.content.trim() : ''

  if (!section || !content) {
    return errorResponse('section and content are required', origin, 400)
  }

  const truncated = content.length > 2000 ? content.slice(0, 2000) + '...' : content

  const prompt = `You are a senior UX writer reviewing a portfolio website section.
Return ONLY valid JSON — no markdown:

{
  "suggestions": [
    { "issue": "<what is weak>", "fix": "<specific improvement>", "impact": "high" | "medium" | "low" }
  ]
}

Provide exactly 3 suggestions. Focus on: clarity, visitor impact, SEO, conversion.

Section: ${section}
Content:
${truncated}`

  const messages: RoleScopedChatInput[] = [
    {
      role: 'system',
      content:
        'You are a helpful assistant. Respond ONLY with valid JSON, no markdown or extra text.'
    },
    { role: 'user', content: prompt }
  ]

  const result = (await env.AI.run(
    MODEL,
    { messages },
    gatewayOptions(env.AI_GATEWAY_ID, false, 86400)
  )) as { response: string }

  try {
    const clean = result.response.replace(/```json|```/g, '').trim()
    return jsonResponse(JSON.parse(clean), origin)
  } catch {
    return jsonResponse({ raw: result.response }, origin)
  }
}

// ── Main fetch handler ────────────────────────────────────────────────────────
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const allowedOrigin = (env.ALLOWED_ORIGIN ?? '*').replace(/\/$/, '')
    const requestOrigin = request.headers.get('Origin') ?? ''
    // Echo back localhost origin so CORS works during local development
    const isLocalhost =
      requestOrigin.startsWith('http://localhost') || requestOrigin.startsWith('http://127.0.0.1')
    const origin = isLocalhost ? requestOrigin : allowedOrigin

    if (request.method === 'OPTIONS') return optionsResponse(origin)
    if (request.method !== 'POST') return errorResponse('Method not allowed', origin, 405)

    // /api/ai/index is protected by its own auth — skip origin check
    const url = new URL(request.url)
    if (url.pathname !== '/api/ai/index' && !isAllowedSource(request, origin)) {
      return errorResponse('Forbidden', origin, 403)
    }

    const contentLength = parseInt(request.headers.get('Content-Length') ?? '0', 10)
    if (contentLength > 10_000) return errorResponse('Request too large', origin, 413)

    let body: Record<string, unknown> = {}
    try {
      body = (await request.json()) as Record<string, unknown>
    } catch {
      return errorResponse('Invalid JSON body', origin, 400)
    }

    try {
      switch (url.pathname) {
        case '/api/ai/chat':
          return handleChat(body, env, origin)
        case '/api/ai/contact':
          return handleContact(body, env, origin)
        case '/api/ai/visitor':
          return handleVisitor(body, env, origin)
        case '/api/ai/analyze':
          return handleAnalyze(body, env, origin)
        case '/api/ai/index':
          return handleIndex(request, env, origin)
        default:
          return errorResponse('Not found', origin, 404)
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Internal server error'
      return errorResponse(message, origin)
    }
  }
}
