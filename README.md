# Theseus
## Web 界面（Flask + Bootstrap）

提供一个简单的网页来运行 `crawl.py --url <target>` 并实时显示终端输出。

1) 安装依赖（建议先激活本项目的虚拟环境 `env`）

2) 运行 Web：

	- Windows PowerShell 示例

	```powershell
	# 激活虚拟环境（如果尚未激活）
	.\env\Scripts\Activate.ps1

	# 安装依赖（初次或更新后）
	pip install -r requirements.txt

	# 启动 Flask Web
	python app.py
	```

3) 打开浏览器访问 http://127.0.0.1:5000 ，输入目标 URL（如 http://localhost），点击“运行”。

4) 日志区域会实时显示 `crawl.py` 在终端的输出，点击“停止”可以结束当前运行。

注意：
 - 需要本机已经可用的 Chrome/Chromedriver 以及 Selenium 运行环境。
 - 如果已在运行中再次点击“运行”，会直接附着到当前进程的输出流；要重新开始请先“停止”。

Smart Web Crawling via Resource-Guided Semantic Modeling

## Running Theseus

```
1. fill .env file

2. python3.11 - m venv env

3. source env/bin/activate

4. pip install -r requirements.txt

5. python3 crawl.py --url http://example.com
```


