import type { GetFamilyTreeResponse, SendInvitationResponse } from '../types/family';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

/**
 * 取得指定使用者的族譜
 */
export async function fetchFamilyTree(userId: string): Promise<GetFamilyTreeResponse> {
  const res = await fetch(`${BASE_URL}/family-tree/me?user_id=${encodeURIComponent(userId)}`);
  if (!res.ok) {
    throw new Error(`取得族譜失敗：${res.status}`);
  }
  return res.json();
}

/**
 * 產生邀請連結
 */
export async function createInvitation(inviterId: string): Promise<SendInvitationResponse> {
  const res = await fetch(`${BASE_URL}/family-tree/invite`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ inviter_id: inviterId }),
  });
  if (!res.ok) {
    throw new Error(`建立邀請失敗：${res.status}`);
  }
  return res.json();
}
