const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'

export type LiffLoginResponse = {
  access_token: string
  token_type: string
  expires_in: number
  line_user_id: string
}

export async function loginWithLiffIdToken(idToken: string): Promise<LiffLoginResponse> {
  const res = await fetch(`${BASE_URL}/api/auth/liff/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id_token: idToken }),
  })

  if (!res.ok) {
    throw new Error(`LIFF 後端登入失敗：${res.status}`)
  }

  return res.json()
}
