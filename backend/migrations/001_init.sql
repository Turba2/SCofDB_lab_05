-- ============================================
-- Схема базы данных маркетплейса
-- ============================================

-- Включаем расширение UUID
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


CREATE TABLE IF NOT EXISTS order_statuses (
    status TEXT PRIMARY KEY,
    description TEXT NOT NULL
);


INSERT INTO order_statuses (status, description)
VALUES
    ('created', 'Order has been created'),
    ('paid', 'Order has been paid'),
    ('cancelled', 'Order has been cancelled'),
    ('shipped', 'Order has been shipped'),
    ('completed', 'Order has been completed')
ON CONFLICT (status) DO NOTHING;


CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT users_email_not_blank CHECK (btrim(email) <> ''),
    CONSTRAINT users_email_valid CHECK (
        email ~ '^[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+$'
    )
);


CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL REFERENCES order_statuses(status),
    total_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT orders_total_amount_non_negative CHECK (total_amount >= 0)
);


CREATE TABLE IF NOT EXISTS order_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_name TEXT NOT NULL,
    price NUMERIC(12, 2) NOT NULL,
    quantity INTEGER NOT NULL,
    subtotal NUMERIC(12, 2) NOT NULL,
    CONSTRAINT order_items_product_name_not_blank CHECK (btrim(product_name) <> ''),
    CONSTRAINT order_items_price_non_negative CHECK (price >= 0),
    CONSTRAINT order_items_quantity_positive CHECK (quantity > 0),
    CONSTRAINT order_items_subtotal_non_negative CHECK (subtotal >= 0),
    CONSTRAINT order_items_subtotal_matches CHECK (subtotal = price * quantity)
);


CREATE TABLE IF NOT EXISTS order_status_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    status TEXT NOT NULL REFERENCES order_statuses(status),
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================
-- КРИТИЧЕСКИЙ ИНВАРИАНТ: Нельзя оплатить заказ дважды
-- ============================================
CREATE OR REPLACE FUNCTION check_order_not_already_paid()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'paid'
       AND OLD.status IS DISTINCT FROM NEW.status
       AND EXISTS (
            SELECT 1
            FROM order_status_history osh
            WHERE osh.order_id = NEW.id
              AND osh.status = 'paid'
       ) THEN
        RAISE EXCEPTION 'Order % cannot be paid twice', NEW.id
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS trigger_check_order_not_already_paid ON orders;
CREATE TRIGGER trigger_check_order_not_already_paid
BEFORE UPDATE OF status ON orders
FOR EACH ROW
EXECUTE FUNCTION check_order_not_already_paid();


-- ============================================
-- БОНУС (опционально)
-- ============================================
-- TODO: Триггер автоматического пересчета total_amount
-- TODO: Триггер автоматической записи в историю при изменении статуса
-- TODO: Триггер записи начального статуса при создании заказа
