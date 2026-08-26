"""Add procurement dashboard, categories, equipment groups, and activation audit.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


_CATEGORIES = (
    ("DRIVE_MOTION", "구동·모션", "모터, 감속기, 실린더와 구동 계통", 10),
    ("SENSOR_MEASUREMENT", "센서·계측", "센서, 계측기, 시험·검사 계통", 20),
    ("ELECTRICAL_CONTROL", "전장·제어", "PLC, 릴레이, 판넬과 전장 제어", 30),
    ("PNEUMATIC_HYDRAULIC", "공압·유압", "밸브, 피팅, 펌프와 유공압 계통", 40),
    ("MATERIAL_HANDLING", "이송·물류", "컨베이어, 팔레트, 체인과 이송 계통", 50),
    ("MECHANICAL_FABRICATION", "기계·제작", "가공품, 프레임과 기계 구조물", 60),
    ("TOOLING_FIXTURE", "치공구·금형", "지그, 치구, 척과 금형", 70),
    ("UTILITY_ENVIRONMENT", "유틸리티·환경", "덕트, 배관, 집진과 냉각 계통", 80),
    ("CABLE_CONNECTOR", "케이블·커넥터", "전선, 케이블, 하네스와 커넥터", 90),
    ("FASTENER_CONSUMABLE", "체결·소모품", "볼트, 너트, 베어링과 소모성 부품", 100),
    ("SAFETY", "안전·보호", "안전장치, 커버와 보호 계통", 110),
    ("IT_NETWORK", "IT·네트워크", "컴퓨터, 네트워크와 소프트웨어", 120),
    ("LABOR_SERVICE", "노무·설치", "설치, 시운전, 설계와 용역", 130),
    ("GENERAL_COMPONENT", "공통 설비·부품", "전문군이 불명확한 산업 설비·부품의 탐색 분류", 900),
)


def upgrade() -> None:
    op.create_table(
        "item_category",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("sort_order >= 0", name="ck_item_category_sort_order"),
        sa.UniqueConstraint("code", name="uq_item_category_code"),
    )
    op.create_table(
        "standard_item_category_assignment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("standard_item_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(100), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("supersedes_assignment_id", sa.Integer()),
        sa.Column("assigned_by", sa.String(100), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 100000000", name="ck_standard_item_category_assignment_confidence"),
        sa.CheckConstraint("json_valid(evidence_json)", name="ck_standard_item_category_assignment_evidence_json"),
        sa.CheckConstraint("supersedes_assignment_id IS NULL OR supersedes_assignment_id <> id", name="ck_standard_item_category_assignment_not_self"),
        sa.ForeignKeyConstraint(["standard_item_id"], ["standard_item.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["category_id"], ["item_category.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["supersedes_assignment_id", "standard_item_id"],
            ["standard_item_category_assignment.id", "standard_item_category_assignment.standard_item_id"],
            name="fk_standard_item_category_assignment_same_item",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("id", "standard_item_id", name="uq_standard_item_category_assignment_evidence_key"),
        sa.UniqueConstraint("supersedes_assignment_id", name="uq_standard_item_category_assignment_supersedes"),
    )
    op.create_index("ix_standard_item_category_assignment_standard_item_id", "standard_item_category_assignment", ["standard_item_id"])
    op.create_index("ix_standard_item_category_assignment_category_id", "standard_item_category_assignment", ["category_id"])

    op.create_table(
        "quote_analysis_equipment_group",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_run_id", sa.Integer(), nullable=False),
        sa.Column("equipment_key", sa.String(255), nullable=False),
        sa.Column("equipment_name", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("quote_amount", sa.BigInteger(), nullable=False),
        sa.Column("target_amount", sa.BigInteger(), nullable=False),
        sa.Column("negotiation_amount", sa.BigInteger(), nullable=False),
        sa.Column("unallocated_amount", sa.BigInteger(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("target_available_count", sa.Integer(), nullable=False),
        sa.Column("mapping_evidence_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("quote_amount >= 0 AND target_amount >= 0 AND negotiation_amount >= 0 AND unallocated_amount >= 0", name="ck_quote_analysis_equipment_amounts_nonnegative"),
        sa.CheckConstraint("line_count >= 0 AND target_available_count >= 0 AND target_available_count <= line_count", name="ck_quote_analysis_equipment_counts"),
        sa.CheckConstraint("json_valid(mapping_evidence_json)", name="ck_quote_analysis_equipment_evidence_json"),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["quote_analysis_run.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("analysis_run_id", "equipment_key", name="uq_quote_analysis_equipment_group_key"),
    )
    op.create_index("ix_quote_analysis_equipment_group_analysis_run_id", "quote_analysis_equipment_group", ["analysis_run_id"])
    op.create_table(
        "quote_analysis_equipment_line",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_run_id", sa.Integer(), nullable=False),
        sa.Column("equipment_group_id", sa.Integer(), nullable=False),
        sa.Column("line_result_id", sa.Integer(), nullable=False),
        sa.Column("raw_item_id", sa.Integer(), nullable=False),
        sa.Column("quote_amount", sa.BigInteger(), nullable=False),
        sa.Column("target_amount", sa.BigInteger()),
        sa.Column("negotiation_amount", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("quote_amount >= 0 AND negotiation_amount >= 0", name="ck_quote_analysis_equipment_line_amounts"),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["quote_analysis_run.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["equipment_group_id"], ["quote_analysis_equipment_group.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["line_result_id"], ["quote_analysis_line_result.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["raw_item_id"], ["raw_quote_item.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("analysis_run_id", "raw_item_id", name="uq_quote_analysis_equipment_line_raw"),
        sa.UniqueConstraint("line_result_id"),
    )
    op.create_index("ix_quote_analysis_equipment_line_analysis_run_id", "quote_analysis_equipment_line", ["analysis_run_id"])
    op.create_index("ix_quote_analysis_equipment_line_equipment_group_id", "quote_analysis_equipment_line", ["equipment_group_id"])
    op.create_index("ix_quote_analysis_equipment_line_raw_item_id", "quote_analysis_equipment_line", ["raw_item_id"])

    op.create_table(
        "quote_catalog_activation_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_run_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("counts_json", sa.Text(), nullable=False),
        sa.Column("activated_by", sa.String(100), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("json_valid(counts_json)", name="ck_quote_catalog_activation_counts_json"),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["quote_analysis_run.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["source_document.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("analysis_run_id", name="uq_quote_catalog_activation_analysis_run"),
    )
    op.create_index("ix_quote_catalog_activation_run_analysis_run_id", "quote_catalog_activation_run", ["analysis_run_id"])
    op.create_index("ix_quote_catalog_activation_run_document_id", "quote_catalog_activation_run", ["document_id"])
    op.create_table(
        "quote_catalog_activation_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("activation_run_id", sa.Integer(), nullable=False),
        sa.Column("raw_item_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("standard_item_id", sa.Integer()),
        sa.Column("standard_price_version_id", sa.Integer()),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.CheckConstraint("json_valid(evidence_json)", name="ck_quote_catalog_activation_entry_evidence_json"),
        sa.ForeignKeyConstraint(["activation_run_id"], ["quote_catalog_activation_run.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["raw_item_id"], ["raw_quote_item.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["standard_item_id"], ["standard_item.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["standard_price_version_id"], ["standard_price_version.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("activation_run_id", "raw_item_id", name="uq_quote_catalog_activation_entry_raw"),
    )
    op.create_index("ix_quote_catalog_activation_entry_activation_run_id", "quote_catalog_activation_entry", ["activation_run_id"])
    op.create_index("ix_quote_catalog_activation_entry_raw_item_id", "quote_catalog_activation_entry", ["raw_item_id"])
    op.create_table(
        "procurement_price_alert",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("activation_run_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("raw_item_id", sa.Integer(), nullable=False),
        sa.Column("standard_item_id", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("current_unit_price", sa.BigInteger(), nullable=False),
        sa.Column("reference_unit_price", sa.BigInteger(), nullable=False),
        sa.Column("difference_percent", sa.BigInteger(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("current_unit_price > 0 AND reference_unit_price > 0", name="ck_procurement_price_alert_prices"),
        sa.ForeignKeyConstraint(["activation_run_id"], ["quote_catalog_activation_run.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["source_document.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["raw_item_id"], ["raw_quote_item.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["standard_item_id"], ["standard_item.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("activation_run_id", "raw_item_id", name="uq_procurement_price_alert_raw"),
    )
    op.create_index("ix_procurement_price_alert_activation_run_id", "procurement_price_alert", ["activation_run_id"])
    op.create_index("ix_procurement_price_alert_document_id", "procurement_price_alert", ["document_id"])
    op.create_index("ix_procurement_price_alert_raw_item_id", "procurement_price_alert", ["raw_item_id"])
    op.create_index("ix_procurement_price_alert_standard_item_id", "procurement_price_alert", ["standard_item_id"])
    op.create_table(
        "alert_notification_delivery",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["alert_id"], ["procurement_price_alert.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("alert_id", "channel", "recipient", name="uq_alert_notification_delivery_target"),
    )
    op.create_index("ix_alert_notification_delivery_alert_id", "alert_notification_delivery", ["alert_id"])

    categories_table = sa.table(
        "item_category",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("sort_order", sa.Integer()),
    )
    op.bulk_insert(
        categories_table,
        [
            {"code": code, "name": name, "description": description, "sort_order": order}
            for code, name, description, order in _CATEGORIES
        ],
    )
    _seed_existing_categories()


def _seed_existing_categories() -> None:
    text_value = "upper(coalesce(v.canonical_name, '') || ' ' || coalesce(v.canonical_spec, ''))"
    cases = (
        ("LABOR_SERVICE", "LABOR|INSTALLATION|INSTALL WORK|COMMISSION|DESIGN WORK|ENGINEER|PROGRAMMING|노무|설치|시운전|설계|인건비|공사비"),
        ("SENSOR_MEASUREMENT", "SENSOR|ENCODER|GAUGE|METER|TESTER|VISION|CAMERA|SCANNER|BARCODE|SCALE|LOAD CELL|TORQUE|PROBE|CALIBRATION|센서|계측|시험|검사|스캐너|측정"),
        ("IT_NETWORK", "COMPUTER|SERVER|NETWORK|ETHERNET|SOFTWARE|MONITOR|CPU|INDUSTRIAL PC|컴퓨터|네트워크|서버|소프트웨어"),
        ("SAFETY", "SAFETY|COVER|GUARD|FENCE|LIGHT CURTAIN|DOOR LOCK|안전|커버|가드|펜스|방호"),
        ("CABLE_CONNECTOR", "CABLE|WIRE|HARNESS|CONNECTOR|TERMINAL|BUS BAR|케이블|전선|하네스|커넥터|단자"),
        ("ELECTRICAL_CONTROL", "PLC|RELAY|INVERTER|INVERTOR|BREAKER|PANEL|CONTROLLER|CONTROL|SMPS|UPS|AVR|AMPLIFIER|ELECTRIC|POWER SUPPLY|SWITCH|BUZZER|TRANSFORMER|BOARD|CARD|HMI|NFB|MCCB|ELCB|전장|제어|차단기|판넬|인버터|전원"),
        ("PNEUMATIC_HYDRAULIC", "VALVE|PNEUMATIC|HYDRAULIC|REGULATOR|PUMP|FITTING|VACUUM|CYLINDER|SOLENOID|MANIFOLD|공압|유압|밸브|펌프|실린더"),
        ("MATERIAL_HANDLING", "CONVEYOR|LOADER|UNLOADER|SHUTTLE|FEEDER|TRANSFER|PALLET|STOCKER|CHAIN|SPROCKET|CHUTE|LIFT|HOIST|HANGER|CARRIER|ROLLER|STOPPER|AGV|컨베이어|이송|적재|팔레트"),
        ("DRIVE_MOTION", "MOTOR|SERVO|GEAR|REDUCER|ACTUATOR|ROBOT|LINEAR MOTION|BALL SCREW|모터|감속|구동|로봇"),
        ("FASTENER_CONSUMABLE", "BOLT|NUT|SCREW|BEARING|WASHER|TAPE|GREASE|O RING|PACKING|볼트|너트|베어링|소모품|와셔"),
        ("UTILITY_ENVIRONMENT", "DUCT|PIPE|FILTER|TANK|FAN|HEATER|COOLER|CHILLER|BLOWER|NOZZLE|EXHAUST|COOLANT|HOSE|SILENCER|집진|덕트|배관|필터|탱크|냉각"),
        ("TOOLING_FIXTURE", "JIG|FIXTURE|CHUCK|GRIPPER|CLAMP|ANVIL|COLLET|DIE|MOLD|TOOLING|지그|치구|금형|척|클램프"),
        ("MECHANICAL_FABRICATION", "FRAME|BRACKET|PLATE|SHAFT|MACHIN|FABRICAT|BASE|BED|RACK|BLOCK|BELT|BUSH|JOINT|TABLE|GUIDE|SPRING|PROFILE|ANGLE|BOX|SUPPORT|POST|BAR|PIN|RING|STEEL|SUS|가공|제작|프레임|구조물|베이스|장치"),
    )
    when_sql: list[str] = []
    confidence_sql: list[str] = []
    for code, tokens in cases:
        predicate = " OR ".join(
            f"{text_value} LIKE '%{token}%'"
            for token in tokens.split("|")
        )
        when_sql.append(f"WHEN {predicate} THEN '{code}'")
        confidence_sql.append(f"WHEN {predicate} THEN 88000000")
    category_case = "CASE " + " ".join(when_sql) + " ELSE 'GENERAL_COMPONENT' END"
    confidence_case = "CASE " + " ".join(confidence_sql) + " ELSE 25000000 END"
    op.execute(sa.text(f"""
        INSERT INTO standard_item_category_assignment
            (standard_item_id, category_id, confidence, method, evidence_json, assigned_by)
        SELECT v.standard_item_id, c.id, {confidence_case},
               'category-keyword-v2',
               json_object('rule', 'category-keyword-v2', 'fallback', ({category_case}) = 'GENERAL_COMPONENT'),
               'migration-0016'
        FROM standard_item_version v
        JOIN (
            SELECT standard_item_id, max(id) AS version_id
            FROM standard_item_version GROUP BY standard_item_id
        ) latest ON latest.version_id = v.id
        JOIN item_category c ON c.code = ({category_case})
    """))


def downgrade() -> None:
    op.drop_table("alert_notification_delivery")
    op.drop_table("procurement_price_alert")
    op.drop_table("quote_catalog_activation_entry")
    op.drop_table("quote_catalog_activation_run")
    op.drop_table("quote_analysis_equipment_line")
    op.drop_table("quote_analysis_equipment_group")
    op.drop_table("standard_item_category_assignment")
    op.drop_table("item_category")
