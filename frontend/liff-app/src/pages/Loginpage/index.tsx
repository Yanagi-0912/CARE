import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import liff from '@line/liff'
import './index.css'

const LIFF_ID = (import.meta.env.VITE_LIFF_ID ?? '').trim()

function LoginPage() {
	const navigate = useNavigate()
	const [statusText, setStatusText] = useState('正在初始化 LINE 登入...')
	const [errorText, setErrorText] = useState('')

	useEffect(() => {
		let cancelled = false

		const initLiff = async () => {
			if (!LIFF_ID) {
				setErrorText('尚未設定 VITE_LIFF_ID，請先完成前端環境變數設定。')
				return
			}

			try {
				await liff.init({ liffId: LIFF_ID })
				if (cancelled) return

				if (!liff.isLoggedIn()) {
					setStatusText('正在導向 LINE 官方登入頁...')
					liff.login({ redirectUri: window.location.href })
					return
				}

				setStatusText('登入成功，正在返回首頁...')
				navigate('/', { replace: true })
			} catch (error) {
				if (cancelled) return
				setErrorText(error instanceof Error ? error.message : 'LIFF 初始化失敗，請稍後再試。')
			}
		}

		void initLiff()

		return () => {
			cancelled = true
		}
	}, [navigate])

	return (
		<main className="login-page">
			<h2 className="login-title">登入 CARE</h2>
			<p className="login-desc">{statusText}</p>
			{errorText && <p className="login-error">{errorText}</p>}
		</main>
	)
}

export default LoginPage
