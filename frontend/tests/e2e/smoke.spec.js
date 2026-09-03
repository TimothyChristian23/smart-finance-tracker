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
  await importPanel.locator('input[name="statement"]').setInputFiles(sampleCsvPath);
  await importPanel.getByPlaceholder("Account label").fill("Chase Checking");
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
  const transactionsPanel = page.getByTestId("transactions-panel");
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

  await transactionsPanel.getByRole("button", { name: "Add" }).click();

  const modal = page.getByTestId("transaction-modal");
  await expect(modal.getByRole("heading", { name: "New Transaction" })).toBeVisible();
  await modal.getByLabel("Date").fill("2026-07-14");
  await modal.getByLabel("Description").fill("Cash Lunch");
  await modal.getByLabel("Amount").fill("-42");
  await modal.getByLabel("Category").selectOption("Dining");
  await modal.getByLabel("Account").fill("Cash");
  await modal.getByRole("button", { name: "Add" }).click();
  await expect(page.getByTestId("status-message")).toHaveText("Added Cash Lunch.");
  await expect(transactionsPanel.getByText("Cash Lunch")).toBeVisible();

  const askPanel = page.getByTestId("ask-panel");
  await askPanel.locator("textarea").fill("How much did I spend on Cash in July 2026?");
  await askPanel.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByTestId("answer-card")).toContainText("Spending for Cash in 2026-07 was $42.00.");

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
  await expect(recurringPanel.getByText("Gym Membership")).toBeVisible();
  await recurringPanel.getByLabel("Hide recurring charge for Gym Membership").click();
  await expect(page.getByTestId("status-message")).toHaveText("Hid recurring charge for Gym Membership.");
  await expect(recurringPanel.getByLabel("Restore recurring charge for Gym Membership")).toBeVisible();
  await recurringPanel.getByLabel("Restore recurring charge for Gym Membership").click();
  await expect(page.getByTestId("status-message")).toHaveText("Restored recurring charge for Gym Membership.");
  await expect(recurringPanel.getByText("Gym Membership")).toBeVisible();
  await expect(transactionsPanel.getByText("Cash Lunch")).toBeVisible();

  await transactionsPanel.getByLabel("Edit Cash Lunch").click();
  await expect(modal.getByRole("heading", { name: "Cash Lunch" })).toBeVisible();
  await modal.getByLabel("Description").fill("Cash Dinner");
  await modal.getByLabel("Amount").fill("-45");
  await modal.getByRole("button", { name: "Save" }).click();
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
