const reasonLabels: Record<string, string> = {
  AMOUNT_MISMATCH: "금액 불일치",
  PRICE_OUTLIER: "단가 편차 큼",
  UNIT_PRICE_MAD_OUTLIER: "단가 편차 큼",
  COLUMN_SHIFT_SUSPECTED: "열 위치 확인 필요",
  INVALID_AMOUNT: "금액 확인 필요",
  INVALID_QUANTITY: "수량 확인 필요",
  NUMERIC_OUT_OF_RANGE: "숫자 범위 확인 필요",
  MISSING_ITEM_NAME: "품명 누락",
  MANUAL_REVIEW: "수동 검토",
  OCR_SOURCE_REVIEW_REQUIRED: "OCR로 추출한 항목 확인 필요",
  PARSER_SOURCE_REVIEW_REQUIRED: "원본 표 구조 확인 필요",
};

export function reasonLabel(reason: string) {
  return reasonLabels[reason] ?? reason;
}
