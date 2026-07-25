const reasonLabels: Record<string, string> = {
  AMOUNT_MISMATCH: "금액 불일치",
  PRICE_OUTLIER: "가격 편차",
  MISSING_ITEM_NAME: "품명 누락",
  MANUAL_REVIEW: "수동 검토",
};

export function reasonLabel(reason: string) {
  return reasonLabels[reason] ?? reason;
}
