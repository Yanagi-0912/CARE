import './index.css'

function Homepage() {
	return (
		<main className="home">
			<section className="home-hero">
				<p className="home-kicker">CARE Assistant</p>
				<h1>把照護與醫療資訊，整理成每一天都能用的決策</h1>
				<p className="home-subtitle">
					你的健康紀錄、衛教內容與提醒，集中在同一個入口。從症狀理解到下一步建議，都更清楚。
				</p>
				<div className="home-actions">
					<button type="button" className="btn btn-primary">
						立即開始
					</button>
					<button type="button" className="btn btn-ghost">
						觀看介紹
					</button>
				</div>
			</section>

			<section className="home-grid" aria-label="CARE 主要功能">
				<article className="feature-card">
					<h2>智慧對話</h2>
					<p>將日常健康問題轉成可行動的建議，包含追蹤重點與就醫提醒。</p>
				</article>
				<article className="feature-card">
					<h2>整合紀錄</h2>
					<p>彙整問答、症狀與檢索內容，建立持續可追蹤的個人健康脈絡。</p>
				</article>
				<article className="feature-card">
					<h2>安心守門</h2>
					<p>透過風險檢核與回應守則，降低錯誤解讀，提供更穩健的資訊支持。</p>
				</article>
			</section>

			<section className="home-band" aria-label="延伸資源">
				<div>
					<p className="band-label">下一步</p>
					<h3>把你的首頁逐步接上真實資料</h3>
					<p>這是視覺初版，下一階段可串接你的 API 與 LIFF 流程。</p>
				</div>
				<a
					className="band-link"
					href="https://developers.line.biz/en/docs/liff/"
					target="_blank"
					rel="noopener noreferrer"
				>
					LIFF 文件
				</a>
			</section>
		</main>
	)
}

export default Homepage
