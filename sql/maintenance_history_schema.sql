-- Manufacturing maintenance/history database schema and seed data.
-- Usage:
--   sqlite3 agent_data/maintenance_history.sqlite < sql/maintenance_history_schema.sql

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS maintenance_history (
    id INTEGER PRIMARY KEY,
    machine_id TEXT NOT NULL,
    event_date TEXT NOT NULL,
    work_type TEXT NOT NULL,
    component TEXT NOT NULL,
    action TEXT NOT NULL,
    technician TEXT,
    downtime_min INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS alarm_logs (
    id INTEGER PRIMARY KEY,
    machine_id TEXT NOT NULL,
    event_time TEXT NOT NULL,
    alarm_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    acknowledged INTEGER DEFAULT 0,
    related_component TEXT
);

CREATE TABLE IF NOT EXISTS sensor_readings (
    id INTEGER PRIMARY KEY,
    machine_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    torque REAL,
    rotational_speed REAL,
    air_temperature REAL,
    process_temperature REAL,
    tool_wear REAL
);

CREATE TABLE IF NOT EXISTS failure_incidents (
    id INTEGER PRIMARY KEY,
    machine_id TEXT NOT NULL,
    event_date TEXT NOT NULL,
    failure_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    root_cause TEXT,
    corrective_action TEXT,
    downtime_min INTEGER DEFAULT 0,
    linked_maintenance_id INTEGER,
    FOREIGN KEY (linked_maintenance_id) REFERENCES maintenance_history(id)
);

CREATE INDEX IF NOT EXISTS idx_maintenance_machine_date
    ON maintenance_history(machine_id, event_date);

CREATE INDEX IF NOT EXISTS idx_alarm_machine_time
    ON alarm_logs(machine_id, event_time);

CREATE INDEX IF NOT EXISTS idx_sensor_machine_time
    ON sensor_readings(machine_id, recorded_at);

CREATE INDEX IF NOT EXISTS idx_incident_machine_date
    ON failure_incidents(machine_id, event_date);

INSERT OR REPLACE INTO maintenance_history
    (id, machine_id, event_date, work_type, component, action, technician, downtime_min, cost, notes)
VALUES
    (1, 'M-1001', '2026-06-18', 'corrective', 'spindle_bearing', '스핀들 베어링 진동 증가로 베어링 교체 및 런아웃 재측정', 'Kim', 180, 1250.0, '가공면 채터 감소 확인'),
    (2, 'M-1001', '2026-06-07', 'preventive', 'coolant_system', '쿨런트 필터 교체 및 유량 점검', 'Park', 45, 180.0, '공정온도 상승 추세 완화'),
    (3, 'M-1001', '2026-05-22', 'inspection', 'tooling', '공구 마모 한계 초과 확인, 엔드밀 교체', 'Lee', 35, 90.0, 'tool_wear 210 이상에서 불량률 증가'),
    (4, 'CNC-02', '2026-06-12', 'corrective', 'axis_servo', 'Y축 서보 알람 후 커넥터 재체결 및 파라미터 백업', 'Choi', 95, 420.0, '간헐 알람 재현 안 됨'),
    (5, 'MILL-03', '2026-06-02', 'preventive', 'guard_interlock', '도어 인터록 스위치 동작 점검 및 배선 정리', 'Han', 30, 60.0, '안전장치 우회 금지 교육 병행'),
    (6, 'M-1001', '2026-04-28', 'corrective', 'drive_belt', '주축 벨트 장력 조정 및 마모 벨트 교체', 'Kim', 70, 260.0, '고부하 운전 시 토크 변동 감소');

INSERT OR REPLACE INTO alarm_logs
    (id, machine_id, event_time, alarm_code, severity, message, acknowledged, related_component)
VALUES
    (1, 'M-1001', '2026-06-19 09:12:00', 'SPN-LOAD-H', 'HIGH', '스핀들 부하 상한 초과', 1, 'spindle'),
    (2, 'M-1001', '2026-06-18 15:44:00', 'VIB-CHT-2', 'MEDIUM', '채터 의심 진동 패턴 감지', 1, 'spindle_bearing'),
    (3, 'M-1001', '2026-06-15 11:03:00', 'TEMP-PROC-H', 'MEDIUM', '공정온도 상승 경고', 1, 'coolant_system'),
    (4, 'CNC-02', '2026-06-12 10:20:00', 'SERVO-Y-ALM', 'HIGH', 'Y축 서보 응답 이상', 1, 'axis_servo'),
    (5, 'MILL-03', '2026-06-03 08:40:00', 'SAFE-DOOR', 'HIGH', '가드 도어 인터록 열림', 1, 'guard_interlock');

INSERT OR REPLACE INTO sensor_readings
    (id, machine_id, recorded_at, torque, rotational_speed, air_temperature, process_temperature, tool_wear)
VALUES
    (1, 'M-1001', '2026-06-19 09:00:00', 62.0, 1320.0, 298.0, 309.0, 215.0),
    (2, 'M-1001', '2026-06-18 14:30:00', 58.0, 1300.0, 300.0, 307.0, 212.0),
    (3, 'M-1001', '2026-06-07 13:10:00', 49.0, 1450.0, 297.0, 302.0, 188.0),
    (4, 'CNC-02', '2026-06-12 10:00:00', 41.0, 1510.0, 296.0, 301.0, 120.0),
    (5, 'MILL-03', '2026-06-03 08:20:00', 35.0, 1600.0, 295.0, 299.0, 80.0);

INSERT OR REPLACE INTO failure_incidents
    (id, machine_id, event_date, failure_type, severity, root_cause, corrective_action, downtime_min, linked_maintenance_id)
VALUES
    (1, 'M-1001', '2026-06-18', 'TWF', 'HIGH', '공구 마모와 스핀들 진동 복합 영향', '공구 교체, 베어링 점검, 절삭 조건 완화', 180, 1),
    (2, 'M-1001', '2026-05-22', 'OSF', 'MEDIUM', '마모 공구 사용으로 토크 상승', '공구 교체 및 토크 기준 재설정', 35, 3),
    (3, 'CNC-02', '2026-06-12', 'PWF', 'HIGH', 'Y축 서보 커넥터 접촉 불량', '커넥터 재체결 및 알람 모니터링', 95, 4);
