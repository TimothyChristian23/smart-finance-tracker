const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { chromium } = require("@playwright/test");

const appUrl = process.env.VISUAL_QA_URL || "http://127.0.0.1:5173/";
const apiUrl = process.env.VISUAL_QA_API_URL || "http://127.0.0.1:8000";
const outputDir = process.env.VISUAL_QA_DIR || path.join(os.tmpdir(), "finance-visual-qa");
const shouldLoadSamples = process.env.VISUAL_QA_LOAD_SAMPLES === "true";

function visibleOverflowScript() {
  const doc = document.documentElement;
  const hasHorizontalScroller = (element) => {
    let current = element.parentElement;
    while (current && current !== document.body) {
      const style = getComputedStyle(current);
      const canScroll = ["auto", "scroll"].includes(style.overflowX) && current.scrollWidth > current.clientWidth + 1;
      if (canScroll) {
        return true;
      }
      current = current.parentElement;
    }
    return false;
  };

  const overflowing = [...document.querySelectorAll("body *")]
    .filter((element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0
        && rect.height > 0
        && style.visibility !== "hidden"
        && rect.right > window.innerWidth + 1
        && !hasHorizontalScroller(element);
    })
    .slice(0, 16)
    .map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        tag: element.tagName,
        className: element.className && String(element.className),
        text: (element.textContent || "").trim().replace(/\s+/g, " ").slice(0, 90),
        right: Math.round(rect.right),
        width: Math.round(rect.width),
      };
    });

  const clippedButtons = [...document.querySelectorAll("button")]
    .filter((element) => element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1)
    .slice(0, 16)
    .map((element) => ({
      text: (element.textContent || "").trim().replace(/\s+/g, " "),
      width: element.clientWidth,
      scrollWidth: element.scrollWidth,
      height: element.clientHeight,
      scrollHeight: element.scrollHeight,
    }));

  return {
    clientWidth: doc.clientWidth,
    scrollWidth: doc.scrollWidth,
    overflowing,
    clippedButtons,
    horizontalScrollers: [...document.querySelectorAll("body *")]
      .filter((element) => {
        const style = getComputedStyle(element);
        return ["auto", "scroll"].includes(style.overflowX) && element.scrollWidth > element.clientWidth + 1;
      })
      .slice(0, 8)
      .map((element) => ({
        tag: element.tagName,
        className: element.className && String(element.className),
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      })),
  };
}

async function waitForApp(page) {
  await page.goto(appUrl, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "Smart Personal Finance Tracker" }).waitFor({ timeout: 15000 });
  await page.getByText("Online", { exact: true }).waitFor({ timeout: 15000 });
}

async function loadSampleDataIfNeeded(page) {
  if (!shouldLoadSamples) {
    return;
  }

  const response = await page.request.post(`${apiUrl}/demo/sample-data`);
  if (!response.ok()) {
    throw new Error(`Sample data request failed with ${response.status()}`);
  }
  await page.reload({ waitUntil: "networkidle" });
  await waitForApp(page);
  await waitForSampleData(page);
}

async function capture(page, name) {
  const filePath = path.join(outputDir, `${name}.png`);
  await page.screenshot({ path: filePath, fullPage: false });
  return filePath;
}

(async () => {
  fs.mkdirSync(outputDir, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const desktop = await browser.newPage({ viewport: { width: 1440, height: 1000 } });

  await waitForApp(desktop);
  const initialDesktopScreenshot = await capture(desktop, "initial-desktop");
  await loadSampleDataIfNeeded(desktop);
  const desktopScreenshot = await capture(desktop, "dashboard-desktop");
  const desktopAudit = await desktop.evaluate(visibleOverflowScript);

  const mobile = await browser.newPage({ viewport: { width: 390, height: 900 } });
  await waitForApp(mobile);
  await waitForSampleData(mobile);
  const mobileScreenshot = await capture(mobile, "dashboard-mobile");
  await mobile.getByTestId("import-panel").scrollIntoViewIfNeeded();
  const mobileImportScreenshot = await capture(mobile, "import-mobile");
  await mobile.getByTestId("transactions-panel").scrollIntoViewIfNeeded();
  const mobileTransactionsScreenshot = await capture(mobile, "transactions-mobile");
  const mobileAudit = await mobile.evaluate(visibleOverflowScript);

  await browser.close();

  const result = {
    appUrl,
    apiUrl,
    outputDir,
    screenshots: {
      initialDesktop: initialDesktopScreenshot,
      dashboardDesktop: desktopScreenshot,
      dashboardMobile: mobileScreenshot,
      importMobile: mobileImportScreenshot,
      transactionsMobile: mobileTransactionsScreenshot,
    },
    audits: {
      desktop: desktopAudit,
      mobile: mobileAudit,
    },
  };

  console.log(JSON.stringify(result, null, 2));

  const hasOverflow = [desktopAudit, mobileAudit].some((audit) => audit.scrollWidth > audit.clientWidth || audit.overflowing.length || audit.clippedButtons.length);
  if (hasOverflow) {
    process.exitCode = 1;
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

async function waitForSampleData(page) {
  if (!shouldLoadSamples) {
    return;
  }

  await page.waitForFunction(
    () => document.body.textContent.includes("Trader Joes"),
    null,
    { timeout: 20000 },
  );
}
