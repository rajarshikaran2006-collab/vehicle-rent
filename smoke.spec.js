import { test, expect } from '@playwright/test';

test('browse and filter the fleet', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /Go further/ })).toBeVisible();
  await page.getByRole('button', { name: 'Electric', exact: true }).click();
  await expect(page.locator('.vehicle-title')).toHaveCount(1);
  await expect(page.locator('.vehicle-title')).toContainText('Nexon EV');
  await page.getByRole('button', { name: 'Search vehicles' }).click();
  await expect(page).toHaveURL(/\/vehicles\?/);
  await expect(page.locator('.listing-grid .vehicle-card').first()).toBeVisible();
  await page.getByLabel('Sort vehicles').selectOption('price_asc');
  await expect(page.locator('.listing-grid .vehicle-card').first()).toBeVisible();
});

test('customer can book, pay in demo mode, download a receipt and cancel', async ({ page }) => {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Customer demo' }).click();
  await expect(page).toHaveURL(/\/account$/);
  await page.goto('/vehicles/4');
  const dates=await page.evaluate(() => {
    const format=d=>{const p=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T10:00`};
    const a=new Date();a.setDate(a.getDate()+60);const b=new Date(a);b.setDate(b.getDate()+2);
    return [format(a),format(b)];
  });
  await page.locator('.booking-panel input[type=datetime-local]').nth(0).fill(dates[0]);
  await page.locator('.booking-panel input[type=datetime-local]').nth(1).fill(dates[1]);
  await expect(page.getByRole('button',{name:'Continue to book'})).toBeEnabled();
  await page.getByRole('button',{name:'Continue to book'}).click();
  await expect(page.getByRole('button',{name:'Continue to payment'})).toBeEnabled();
  await page.getByRole('button',{name:'Continue to payment'}).click();
  await expect(page.getByText('Demo payments only')).toBeVisible();
  await page.getByRole('button',{name:/Pay ₹/}).click();
  await expect(page.getByRole('heading',{name:'You’re going places.'})).toBeVisible();
  const ref=await page.locator('.confirmation-card > p strong').innerText();
  const downloadPromise=page.waitForEvent('download');
  await page.getByRole('link',{name:'Download receipt'}).click();
  expect((await downloadPromise).suggestedFilename()).toMatch(/\.pdf$/);
  await page.getByRole('link',{name:'View my bookings'}).click();
  const card=page.locator('.booking-card').filter({hasText:ref});
  await expect(card).toBeVisible();
  page.once('dialog', d=>d.accept());
  await card.getByRole('button',{name:'Cancel'}).click();
  await page.getByRole('link',{name:'Booking history'}).click();
  await expect(page.locator('.booking-card').filter({hasText:ref}).getByText('Cancelled')).toBeVisible();
});

test('admin workspace loads real metrics and reports', async ({ page }) => {
  await page.goto('/login');
  await page.getByRole('button',{name:'Admin demo'}).click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole('heading',{name:'Dashboard overview'})).toBeVisible();
  await expect(page.getByText('TOTAL VEHICLES')).toBeVisible();
  await page.getByRole('link',{name:'Reports',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Reports & analytics'})).toBeVisible();
  await page.getByLabel('Report period').selectOption('yearly');
  await expect(page.getByText('Paid in selected period')).toBeVisible();
  const downloadPromise=page.waitForEvent('download');
  await page.getByRole('link',{name:'Export CSV'}).click();
  expect((await downloadPromise).suggestedFilename()).toMatch(/\.csv$/);
});

test('staff workspace limits financial and customer-account access', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email address').fill('staff@roam.test');
  await page.getByLabel('Password', { exact: true }).fill('Staff123!');
  await page.getByRole('button',{name:'Log in',exact:true}).click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole('heading',{name:'Dashboard overview'})).toBeVisible();
  await expect(page.getByText('TOTAL REVENUE')).toHaveCount(0);
  await expect(page.getByRole('link',{name:'Customers',exact:true})).toHaveCount(0);
  await page.getByRole('link',{name:'Fleet',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Vehicle fleet'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Add vehicle'})).toHaveCount(0);
});
