import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../../..");
const sampleCsvPath = path.join(repoRoot, "data", "sample_transactions.csv");
const sampleRecurringCsvPath = path.join(repoRoot, "data", "sample_recurring_transactions.csv");
const sampleTransactionCount = fs.readFileSync(sampleCsvPath, "utf8").trim().split(/\r?\n/).length - 1;
const apiBase = process.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

test.beforeEach(async ({ request }) => {
  await request.delete(`${apiBase}/data?confirmation=RESET`);
});

test.afterEach(async ({ request }) => {
  await request.delete(`${apiBase}/data?confirmation=RESET`);
});

test("imports, edits, deletes, restores, and answers from the UI", async ({ page, request }, testInfo) => {
  page.on("dialog", (dialog) => dialog.accept());

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Smart Personal Finance Tracker" })).toBeVisible();
  await expect(page.getByText("Online")).toBeVisible();

  const importPanel = page.getByTestId("import-panel");
  const customCsvPath = testInfo.outputPath("custom-bank.csv");
  fs.writeFileSync(
    customCsvPath,
    [
      "Posted,Payee,Outflow,Inflow,Bucket,Wallet",
      "10/01/2026,Farmers Market,42.37,,Food & Grocery,Travel Checking",
      "10/02/2026,Payroll Deposit,,3200.00,Income,Travel Checking",
    ].join("\n"),
  );

  const presetPanel = page.getByTestId("csv-preset-panel");
  await presetPanel.getByLabel("Preset name").fill("Travel Checking");
  await presetPanel.getByLabel("Date column").fill("Posted");
  await presetPanel.getByLabel("Description column").fill("Payee");
  await presetPanel.getByLabel("Debit column").fill("Outflow");
  await presetPanel.getByLabel("Credit column").fill("Inflow");
  await presetPanel.getByLabel("Category column").fill("Bucket");
  await presetPanel.getByLabel("Account column").fill("Wallet");
  await presetPanel.getByRole("button", { name: "Save Mapping" }).click();
  await expect(page.getByTestId("status-message")).toHaveText("Saved CSV mapping Travel Checking.");

  await importPanel.locator('input[name="statement"]').setInputFiles(customCsvPath);
  await importPanel.getByLabel("CSV mapping preset").selectOption({ label: "Travel Checking" });
  await importPanel.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByTestId("status-message")).toHaveText("Previewed 2 rows from custom-bank.csv.");
  await expect(importPanel.getByText("Farmers Market", { exact: true })).toBeVisible();

  await importPanel.locator('input[name="statement"]').setInputFiles(sampleCsvPath);
  await importPanel.getByPlaceholder("Account label").fill("Chase Checking");
  await importPanel.getByLabel("CSV mapping preset").selectOption("");
  await importPanel.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByTestId("status-message")).toHaveText(
    `Previewed ${sampleTransactionCount} rows from sample_transactions.csv.`,
  );
  await expect(importPanel.getByText(`${sampleTransactionCount} importable`)).toBeVisible();
  await importPanel.getByLabel("Category for Trader Joes").first().selectOption("Dining");
  await expect(importPanel.getByText("Manual review: Category selected during import review.")).toBeVisible();

  await importPanel.getByRole("button", { name: "Import Reviewed" }).click();
  await expect(page.getByTestId("status-message")).toContainText(`Imported ${sampleTransactionCount} reviewed transactions`);

  await page.getByLabel("Month").selectOption("2026-07");
  const qualityPanel = page.getByTestId("quality-panel");
  await expect(qualityPanel.locator(".quality-summary strong")).toHaveText("Needs Review");
  await expect(qualityPanel.getByText(/transactions available for this view/)).toBeVisible();
  const transactionsPanel = page.getByTestId("transactions-panel");
  const modal = page.getByTestId("transaction-modal");
  await expect(transactionsPanel.getByText("Trader Joes")).toBeVisible();

  const rulesPanel = page.getByTestId("rules-panel");
  await rulesPanel.getByLabel("Rule merchant").fill("Amazon Marketplace");
  await rulesPanel.getByLabel("Rule category").selectOption("Subscriptions");
  await rulesPanel.getByLabel("Apply rule to existing matching transactions").check();
  await rulesPanel.getByRole("button", { name: "Save Rule" }).click();
  await expect(page.getByTestId("status-message")).toHaveText(
    "Saved Subscriptions rule for Amazon Marketplace and updated 1 transaction.",
  );
  await expect(rulesPanel.getByText("Amazon Marketplace")).toBeVisible();
  await transactionsPanel.getByLabel("Search transactions").fill("Amazon");
  await expect(transactionsPanel.getByLabel("Category for Amazon Marketplace")).toHaveValue("Subscriptions");
  await transactionsPanel.getByLabel("Search transactions").fill("");

  await transactionsPanel.getByLabel("Edit Amazon Marketplace").click();
  await expect(modal.getByRole("heading", { name: "Amazon Marketplace" })).toBeVisible();
  await modal.getByRole("button", { name: "Add Split" }).click();
  await modal.getByLabel("Split 1 category").selectOption("Dining");
  await modal.getByLabel("Split 1 amount").fill("25.20");
  await modal.getByLabel("Split 1 note").fill("Lunch supplies");
  await modal.getByRole("button", { name: "Add Split" }).click();
  await modal.getByLabel("Split 2 category").selectOption("Subscriptions");
  await modal.getByLabel("Split 2 amount").fill("40");
  await expect(modal.getByText("Balanced")).toBeVisible();
  await modal.getByRole("button", { name: "Save Splits" }).click();
  await expect(page.getByTestId("status-message")).toHaveText("Split Amazon Marketplace across 2 categories.");
  await modal.getByLabel("Close transaction form").click();
  await expect(transactionsPanel.getByText("Split: Dining $25.20 | Subscriptions $40.00")).toBeVisible();

  const askPanel = page.getByTestId("ask-panel");
  await askPanel.locator("textarea").fill("How much did I spend on dining in July 2026?");
  await askPanel.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByTestId("answer-card")).toContainText("You spent $171.19 on Dining for 2026-07.");

  await transactionsPanel.getByRole("button", { name: "Add" }).click();
  await expect(modal.getByRole("heading", { name: "New Transaction" })).toBeVisible();
  await modal.getByLabel("Date").fill("2026-07-14");
  await modal.getByLabel("Description").fill("Cash Lunch");
  await modal.getByLabel("Amount").fill("-42");
  await modal.getByLabel("Category").selectOption("Dining");
  await modal.getByLabel("Account").fill("Cash");
  await modal.getByRole("button", { name: "Add" }).click();
  await expect(page.getByTestId("status-message")).toHaveText("Added Cash Lunch.");
  await expect(transactionsPanel.getByText("Cash Lunch")).toBeVisible();

  await askPanel.locator("textarea").fill("How much did I spend on Cash in July 2026?");
  await askPanel.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByTestId("answer-card")).toContainText("Spending for Cash in 2026-07 was $42.00.");

  const anomaliesPanel = page.getByTestId("anomalies-panel");
  await expect(anomaliesPanel.getByText("One-Time Electronics Store")).toBeVisible();
  await anomaliesPanel.getByLabel("Dismiss anomaly for One-Time Electronics Store").click();
  await expect(page.getByTestId("status-message")).toHaveText("Dismissed anomaly for One-Time Electronics Store.");
  await expect(anomaliesPanel.getByLabel("Restore anomaly for One-Time Electronics Store")).toBeVisible();
  await anomaliesPanel.getByLabel("Restore anomaly for One-Time Electronics Store").click();
  await expect(page.getByTestId("status-message")).toHaveText("Restored anomaly for One-Time Electronics Store.");
  await expect(anomaliesPanel.getByText("One-Time Electronics Store")).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await importPanel.getByRole("button", { name: "Backup" }).click();
  const download = await downloadPromise;
  const backupPath = testInfo.outputPath(download.suggestedFilename());
  await download.saveAs(backupPath);

  const recurringUpload = await request.post(`${apiBase}/transactions/upload`, {
    multipart: {
      file: {
        name: "sample_recurring_transactions.csv",
        mimeType: "text/csv",
        buffer: fs.readFileSync(sampleRecurringCsvPath),
      },
    },
  });
  expect(recurringUpload.ok()).toBeTruthy();

  await page.reload();
  await expect(page.getByText("Online")).toBeVisible();
  await page.getByLabel("Month").selectOption("2026-07");
  const recurringPanel = page.getByTestId("recurring-panel");
  const billPanel = page.getByTestId("bill-calendar-panel");
  await expect(billPanel.getByText("Gym Membership")).toBeVisible();
  await expect(billPanel.locator(".bill-calendar-total strong")).toHaveText("$44.67");
  await expect(recurringPanel.getByText("Gym Membership")).toBeVisible();
  await recurringPanel.getByLabel("Hide recurring charge for Gym Membership").click();
  await expect(page.getByTestId("status-message")).toHaveText("Hid recurring charge for Gym Membership.");
  await expect(recurringPanel.getByLabel("Restore recurring charge for Gym Membership")).toBeVisible();
  await expect(billPanel.getByText("Gym Membership")).toHaveCount(0);
  await expect(billPanel.getByText("No expected bills for 2026-09.")).toBeVisible();
  await recurringPanel.getByLabel("Restore recurring charge for Gym Membership").click();
  await expect(page.getByTestId("status-message")).toHaveText("Restored recurring charge for Gym Membership.");
  await expect(recurringPanel.getByText("Gym Membership")).toBeVisible();
  await expect(billPanel.getByText("Gym Membership")).toBeVisible();
  await expect(transactionsPanel.getByText("Cash Lunch")).toBeVisible();

  await transactionsPanel.getByLabel("Edit Cash Lunch").click();
  await expect(modal.getByRole("heading", { name: "Cash Lunch" })).toBeVisible();
  await modal.getByLabel("Description").fill("Cash Dinner");
  await modal.getByLabel("Amount").fill("-45");
  await modal.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByTestId("status-message")).toHaveText("Updated Cash Dinner.");
  await expect(transactionsPanel.getByText("Cash Dinner")).toBeVisible();

  await transactionsPanel.getByLabel("Delete Cash Dinner").click();
  await expect(page.getByTestId("status-message")).toHaveText("Deleted Cash Dinner.");
  await expect(transactionsPanel.getByLabel("Edit Cash Dinner")).toHaveCount(0);

  const privacyPanel = page.getByTestId("privacy-panel");
  await privacyPanel.getByLabel("Reset confirmation").fill("RESET");
  await privacyPanel.getByRole("button", { name: "Reset Data" }).click();
  await expect(page.getByTestId("status-message")).toHaveText("All local finance data cleared.");
  await expect(transactionsPanel.getByText("No matching transactions.")).toBeVisible();

  await privacyPanel.locator('input[name="backup"]').setInputFiles(backupPath);
  await privacyPanel.getByLabel("Restore confirmation").fill("RESTORE");
  await privacyPanel.getByRole("button", { name: "Restore" }).click();
  await expect(page.getByTestId("status-message")).toHaveText(
    `Restored ${sampleTransactionCount + 1} transactions from backup.`,
  );
  await page.getByLabel("Month").selectOption("2026-07");
  await expect(transactionsPanel.getByText("Cash Lunch")).toBeVisible();
});
