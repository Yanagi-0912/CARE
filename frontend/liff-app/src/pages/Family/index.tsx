import { useEffect, useState, useCallback } from 'react';
import liff from '@line/liff';
import { useI18n } from '../../i18n';
import { fetchFamilyTree, createInvitation } from '../../api/familyApi';
import { RELATIONSHIP_LABEL } from '../../types/family';
import type { FamilyMember } from '../../types/family';
import './index.css';

/** LIFF App ID — 由環境變數注入，或留空讓 init 時跳過 */
const LIFF_ID = import.meta.env.VITE_LIFF_ID || '';

/** 暫用 mock userId（MVP）；後續整合 LIFF 登入後由 liff.getProfile() 取得 */
const MOCK_USER_ID = '123456789';

const FamilyPage = () => {
  const { t } = useI18n();

  /* ── state ─────────────────────────────────────────── */
  const [members, setMembers]   = useState<FamilyMember[]>([]);
  const [loading, setLoading]   = useState(true);
  const [inviting, setInviting] = useState(false);
  const [toast, setToast]       = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  /* ── toast 自動消失 ────────────────────────────────── */
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 3000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  /* ── 載入族譜 ──────────────────────────────────────── */
  useEffect(() => {
    (async () => {
      try {
        const res = await fetchFamilyTree(MOCK_USER_ID);
        setMembers(res.family_tree.family_members);
      } catch (err) {
        console.error('載入族譜失敗:', err);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  /* ── 邀請流程 ──────────────────────────────────────── */
  const handleInvite = useCallback(async () => {
    setInviting(true);
    try {
      // 1. 後端產生邀請連結
      const { invite_url } = await createInvitation(MOCK_USER_ID);

      // 2. 初始化 LIFF（若尚未初始化）
      if (LIFF_ID && !liff.isInClient()) {
        try {
          await liff.init({ liffId: LIFF_ID });
        } catch (initErr) {
          console.warn('LIFF init 略過（非 LINE 環境）:', initErr);
        }
      }

      // 3. 組合 Flex Message 並呼叫 shareTargetPicker
      if (liff.isApiAvailable('shareTargetPicker')) {
        await liff.shareTargetPicker([
          {
            type: 'flex',
            altText: t('family.shareTitle'),
            contents: {
              type: 'bubble',
              hero: {
                type: 'image',
                url: 'https://developers.line.biz/assets/images/services/bot-designer-icon.png',
                size: 'full',
                aspectRatio: '20:13',
                aspectMode: 'cover',
              },
              body: {
                type: 'box',
                layout: 'vertical',
                contents: [
                  {
                    type: 'text',
                    text: t('family.shareTitle'),
                    weight: 'bold',
                    size: 'lg',
                  },
                  {
                    type: 'text',
                    text: t('family.shareDesc'),
                    size: 'sm',
                    color: '#999999',
                    margin: 'md',
                    wrap: true,
                  },
                ],
              },
              footer: {
                type: 'box',
                layout: 'vertical',
                spacing: 'sm',
                contents: [
                  {
                    type: 'button',
                    style: 'primary',
                    color: '#06c755',
                    action: {
                      type: 'uri',
                      label: t('family.inviteBtn'),
                      uri: invite_url,
                    },
                  },
                ],
              },
            },
          },
        ]);
        setToast({ msg: t('family.inviteSuccess'), type: 'success' });
      } else {
        // 非 LINE 環境：複製連結到剪貼簿
        await navigator.clipboard.writeText(invite_url);
        setToast({ msg: `邀請連結已複製：${invite_url}`, type: 'success' });
      }
    } catch (err) {
      console.error('邀請失敗:', err);
      setToast({ msg: t('family.inviteError'), type: 'error' });
    } finally {
      setInviting(false);
    }
  }, [t]);

  /* ── 顯示名稱（MVP：user_id 前 6 碼；方案 A 擴充後用 display_name）── */
  const getDisplayName = (m: FamilyMember) => m.display_name || m.user_id.slice(0, 8);

  /* ── 顯示稱謂 ──────────────────────────────────────── */
  const getRelationLabel = (m: FamilyMember) => {
    if (!m.relationship_type) return t('family.unset');
    return RELATIONSHIP_LABEL[m.relationship_type] || m.relationship_type;
  };

  /* ── render ────────────────────────────────────────── */
  return (
    <div className="family-page">
      {/* Toast */}
      {toast && (
        <div className={`family-toast ${toast.type}`}>{toast.msg}</div>
      )}

      {/* 標題列 */}
      <header className="family-header">
        <h2>👥 {t('family.title')}</h2>
        <button
          id="family-invite-btn"
          className="invite-btn"
          onClick={handleInvite}
          disabled={inviting}
        >
          {inviting ? '⏳' : '➕'} {t('family.inviteBtn')}
        </button>
      </header>

      {/* 內容區 */}
      {loading ? (
        <div className="family-empty">
          <p>載入中…</p>
        </div>
      ) : members.length === 0 ? (
        <div className="family-empty">
          <div className="empty-icon">👨‍👩‍👧‍👦</div>
          <p>{t('family.empty')}</p>
        </div>
      ) : (
        <section className="member-grid">
          {members.map((m) => (
            <div key={m.user_id} className="member-card">
              {/* 頭像 */}
              <div className="member-avatar">
                {m.picture_url ? (
                  <img src={m.picture_url} alt={getDisplayName(m)} />
                ) : (
                  '👤'
                )}
              </div>
              {/* LINE 名稱 */}
              <span className="member-name">{getDisplayName(m)}</span>
              {/* 稱謂 */}
              <span className={`member-relation ${!m.relationship_type ? 'unset' : ''}`}>
                {getRelationLabel(m)}
              </span>
            </div>
          ))}
        </section>
      )}
    </div>
  );
};

export default FamilyPage;
