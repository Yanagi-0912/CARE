import './index.css'

function LoginPage() {
	return (
		<main className="login-page">
			<h2 className="login-title">登入 CARE</h2>
			<p className="login-desc">
				請登入以查看您的專屬健康資訊
			</p>

			<button className="line-login-btn">
				使用 LINE 帳號登入
			</button>
		</main>
	)
}

export default LoginPage
