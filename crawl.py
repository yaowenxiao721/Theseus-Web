import multiprocessing
import argparse

from Classes import *
from Functions import add_script

from selenium.webdriver.remote.webdriver import WebDriver
from seleniumwire import webdriver

from llm_analysis import run_llm_analysis

parser = argparse.ArgumentParser(description='Crawler')
parser.add_argument("--debug", action='store_true',
                    help="Dont use path deconstruction and recon scan. Good for testing single URL")
parser.add_argument("--url", help="Custom URL to crawl")
args = parser.parse_args()

os.environ["TOKENIZERS_PARALLELISM"] = "false"

def main():
    if args.url:
        request_queue = multiprocessing.Queue()
        analysis_queue = multiprocessing.Queue()
        condition_signal = multiprocessing.Event()
        still_crawling_signal = multiprocessing.Event()

        still_crawling_signal.set()

        llm_process = multiprocessing.Process(target=run_llm_analysis, args=(request_queue, analysis_queue, condition_signal, still_crawling_signal))
        llm_process.start()

        root_dir_name = os.path.dirname(__file__)
        dynamic_path = os.path.join(root_dir_name, 'form_files', 'dynamic')
        for f in os.listdir(dynamic_path):
            os.remove(os.path.join(dynamic_path, f))

        WebDriver.add_script = add_script

        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument("--disable-pre-commit-input")
        chrome_options.add_argument("--disable-features=AllowPreCommitInput")
        chrome_options.add_argument("--disable-xss-auditor")
        chrome_options.add_argument("--disable-ipc-flooding-protection")
        chrome_options.add_argument("--no-experiments")
        chrome_options.add_argument("--disable-bundled-ppapi-flash")
        chrome_options.add_argument("--disable-plugins-discovery")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_argument('--ignore-certificate-errors')
        # chrome_options.add_argument('--headless')
        chrome_options.add_argument("--no-sandbox")

        driver = webdriver.Chrome(options=chrome_options)

        # chrome_options.add_argument("--disable-dev-shm-usage")
        #
        # try:
        #     # 使用配置好的 options 初始化 driver
        #     driver = webdriver.Chrome(options=chrome_options)
        #
        #     # ... 您的爬虫代码 ...
        #     print("浏览器启动成功！")
        #     # driver.get(...)
        #
        # finally:
        #     if 'driver' in locals() and driver:
        #         driver.quit()



        # Read scripts and add script which will be executed when the page starts loading
        ## JS libraries from JaK crawler, with minor improvements
        driver.add_script(open("js/lib.js", "r").read())
        driver.add_script(open("js/property_obs.js", "r", encoding='utf-8').read())
        driver.add_script(open("js/md5.js", "r").read())
        driver.add_script(open("js/addeventlistener_wrapper.js", "r").read())
        driver.add_script(open("js/timing_wrapper.js", "r").read())
        driver.add_script(open("js/window_wrapper.js", "r").read())
        # Black Widow additions
        driver.add_script(open("js/forms.js", "r").read())
        driver.add_script(open("js/xss_xhr.js", "r").read())
        driver.add_script(open("js/remove_alerts.js", "r").read())

        url = args.url
        crawler = Crawler(driver, url, request_queue, analysis_queue, condition_signal, still_crawling_signal)

        asyncio.get_event_loop().run_until_complete(crawler.start(args.debug))

    else:
        print("Please use --url")

if __name__ == "__main__":
    main()
