const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'

export type UpsertPersonalHealthPayload = {
    name: string
    gender: string
    height: number
    weight: number
    age: number
    chronic_history: string
    major_illness_history: string
    surgery_history: string
    health_consultations: Record<string, unknown>
}

export async function upsertPersonalHealthProfile(
    userId: string,
    payload: UpsertPersonalHealthPayload,
) {
    const token = (localStorage.getItem('CARE_AUTH_TOKEN') || '').trim()
    if (!token) {
        throw new Error('缺少登入憑證，請先重新登入')
    }

    const res = await fetch(`${BASE_URL}/profiles/${userId}`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
    })

    if (!res.ok) {
        const text = await res.text().catch(() => '')
        throw new Error(`個人資料儲存失敗：${res.status}${text ? ` - ${text}` : ''}`)
    }

    return res.json()
}

export async function getPersonalHealthProfile(userId: string) {
    const token = (localStorage.getItem('CARE_AUTH_TOKEN') || '').trim()
    if (!token) {
        throw new Error('缺少登入憑證，請先重新登入')
    }

    const res = await fetch(`${BASE_URL}/profiles/${userId}`, {
        method: 'GET',
        headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
        },
    })

    if (!res.ok) {
        const text = await res.text().catch(() => '')
        throw new Error(`取得個人資料失敗：${res.status}${text ? ` - ${text}` : ''}`)
    }

    return res.json()
}
