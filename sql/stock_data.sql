DROP TABLE IF EXISTS stock_ticks_realtime;
DROP TABLE IF EXISTS stock_daily_prices;
DROP TABLE IF EXISTS stocks;

CREATE TABLE stocks (
    stock_id SERIAL PRIMARY KEY,
    ticker VARCHAR(10) UNIQUE NOT NULL,
    company_name VARCHAR(150) NOT NULL,
    floor VARCHAR(10) DEFAULT 'HOSE',
    reference_price NUMERIC(10, 2) NOT NULL
);

CREATE TABLE stock_daily_prices (
    id SERIAL PRIMARY KEY,
    stock_id INT REFERENCES stocks(stock_id) ON DELETE CASCADE,
    price_date DATE NOT NULL,
    open_price NUMERIC(10, 2) NOT NULL,
    high_price NUMERIC(10, 2) NOT NULL,
    low_price NUMERIC(10, 2) NOT NULL,
    close_price NUMERIC(10, 2) NOT NULL,
    volume BIGINT NOT NULL,
    CONSTRAINT unique_stock_daily UNIQUE (stock_id, price_date)
);

CREATE TABLE stock_ticks_realtime (
    tick_id BIGSERIAL PRIMARY KEY,
    stock_id INT REFERENCES stocks(stock_id) ON DELETE CASCADE,
    tick_time TIMESTAMP NOT NULL,
    match_price NUMERIC(10, 2) NOT NULL,
    match_volume INT NOT NULL,
    order_type VARCHAR(1) CHECK (order_type IN ('B', 'S'))
);

INSERT INTO stocks (ticker, company_name, floor, reference_price) VALUES
('FPT', 'Cong ty Co phan FPT', 'HOSE', 135000.00),
('VCB', 'Ngan hang Vietcombank', 'HOSE', 92000.00),
('VHM', 'Vinhomes', 'HOSE', 43000.00),
('HPG', 'Tap doan Hoa Phat', 'HOSE', 26000.00);

INSERT INTO public.stock_daily_prices (stock_id, price_date, open_price, high_price, low_price, close_price, volume) VALUES
(1, '2026-10-24', 134500.00, 136000.00, 134000.00, 135200.00, 2500000),
(1, '2026-10-25', 135200.00, 135800.00, 133900.00, 134100.00, 1800000),
(1, '2026-10-26', 134100.00, 137200.00, 134100.00, 136800.00, 3100000),
(2, '2026-10-26', 91500.00, 92800.00, 91200.00, 92200.00, 1200000),
(4, '2026-10-26', 25800.00, 26400.00, 25650.00, 26100.00, 8500000)
ON CONFLICT (stock_id, price_date) DO NOTHING;

CREATE OR REPLACE PROCEDURE generate_mock_stock_data(
    p_ticker VARCHAR, 
    p_minutes_back INT
)
LANGUAGE plpgsql AS $$
DECLARE
    v_stock_id INT;
    v_current_price NUMERIC(10,2);
    v_time TIMESTAMP;
    v_change_pct NUMERIC;
    v_rand_vol INT;
    v_type VARCHAR(1);
BEGIN
    SELECT stock_id, reference_price INTO v_stock_id, v_current_price 
    FROM stocks WHERE ticker = p_ticker;
    
    IF v_stock_id IS NULL THEN
        RAISE EXCEPTION 'Ticker % not found', p_ticker;
    END IF;

    v_time := NOW() - (p_minutes_back || ' minutes')::INTERVAL;

    FOR i IN 1..(p_minutes_back * 60) LOOP
        v_change_pct := (random() * 2 - 1) * 0.005; 
        v_current_price := ROUND(v_current_price * (1 + v_change_pct), 2);
        v_rand_vol := (FLOOR(random() * 50) + 1) * 100;
        
        IF random() > 0.5 THEN v_type := 'B'; ELSE v_type := 'S'; END IF;

        INSERT INTO stock_ticks_realtime (stock_id, tick_time, match_price, match_volume, order_type)
        VALUES (v_stock_id, v_time, v_current_price, v_rand_vol, v_type);

        v_time := v_time + INTERVAL '1 second';
    END LOOP;
END;
$$;

CALL generate_mock_stock_data('FPT', 30);
CALL generate_mock_stock_data('HPG', 15);

SELECT t.tick_time, s.ticker, t.match_price, t.match_volume, t.order_type
FROM stock_ticks_realtime t
JOIN stocks s ON t.stock_id = s.stock_id
ORDER BY t.tick_time DESC
LIMIT 50;

SELECT 
    s.ticker,
    DATE(t.tick_time) AS price_date,
    (SELECT match_price FROM stock_ticks_realtime WHERE stock_id = s.stock_id ORDER BY tick_time ASC LIMIT 1) AS open_price,
    MAX(t.match_price) AS high_price,
    MIN(t.match_price) AS low_price,
    (SELECT match_price FROM stock_ticks_realtime WHERE stock_id = s.stock_id ORDER BY tick_time DESC LIMIT 1) AS close_price,
    SUM(t.match_volume) AS total_volume
FROM stock_ticks_realtime t
JOIN stocks s ON t.stock_id = s.stock_id
GROUP BY s.ticker, s.stock_id, DATE(t.tick_time);
