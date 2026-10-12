export function aggregateByItemName(purchases, resellRatio = 0.85) {
  const ratio = Math.max(0.01, Math.min(1, Number(resellRatio) || 0.85));
  const byName = new Map();
  for (const t of purchases) {
    const name = (t.name || "—").toString();
    if (!byName.has(name)) {
      byName.set(name, {
        name,
        count: 0,
        totalPrice: 0,
        totalMp: 0,
        mpCount: 0,
        soldCount: 0,
        totalSalePrice: 0,
        totalDiscountRatio: 0,
        totalCashProfit: 0,
        totalDeviation: 0,
        totalSoldMp: 0,
        deviationCount: 0,
      });
    }
    const r = byName.get(name);
    r.count += 1;
    r.totalPrice += Number(t.price) || 0;
    if (t.market_price != null) {
      r.totalMp += Number(t.market_price);
      r.mpCount += 1;
    }
    const sold = t.sale_price != null && Number(t.sale_price) > 0;
    if (sold) {
      const saleP = Number(t.sale_price);
      const cost = Number(t.price) || 0;
      const mp = t.market_price != null ? Number(t.market_price) : 0;
      const afterTax = saleP / 1.15;
      r.soldCount += 1;
      r.totalSalePrice += saleP;
      if (afterTax > 0 && cost > 0) r.totalDiscountRatio += cost / afterTax;
      r.totalCashProfit += afterTax * ratio - cost;
      if (mp > 0) {
        r.totalDeviation += saleP - mp;
        r.totalSoldMp += mp;
        r.deviationCount += 1;
      }
    }
  }
  return Array.from(byName.values())
    .map((r) => ({
      name: r.name,
      count: r.count,
      avgPrice: r.count > 0 ? r.totalPrice / r.count : 0,
      avgMp: r.mpCount > 0 ? r.totalMp / r.mpCount : null,
      totalSaleAmount: r.soldCount > 0 ? r.totalSalePrice : null,
      avgSalePrice: r.soldCount > 0 ? r.totalSalePrice / r.soldCount : null,
      avgDiscountRatio:
        r.soldCount > 0 && r.totalDiscountRatio > 0
          ? r.totalDiscountRatio / r.soldCount
          : null,
      totalCashProfit: r.soldCount > 0 ? r.totalCashProfit : null,
      avgDeviation:
        r.deviationCount > 0 ? r.totalDeviation / r.deviationCount : null,
      avgDeviationPct:
        r.deviationCount > 0 && r.totalSoldMp > 0
          ? (r.totalDeviation / r.totalSoldMp) * 100
          : null,
    }))
    .sort((a, b) => b.count - a.count);
}

