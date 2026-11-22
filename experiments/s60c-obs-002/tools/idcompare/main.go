package main

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"reflect"
	"strconv"
	"time"

	"github.com/go-sql-driver/mysql"
)

type result struct {
	columns []string
	rows    [][][]byte
	err     bool
}

func read(rows *sql.Rows, err error) result {
	if err != nil {
		return result{err: true}
	}
	defer rows.Close()
	cols, err := rows.Columns()
	if err != nil {
		return result{err: true}
	}
	r := result{columns: cols, rows: [][][]byte{}}
	for rows.Next() {
		vals := make([]sql.RawBytes, len(cols))
		ptr := make([]any, len(cols))
		for i := range vals {
			ptr[i] = &vals[i]
		}
		if rows.Scan(ptr...) != nil {
			return result{err: true}
		}
		row := make([][]byte, len(cols))
		for i, v := range vals {
			if v != nil {
				row[i] = append([]byte{}, v...)
			}
		}
		r.rows = append(r.rows, row)
	}
	if rows.Err() != nil {
		return result{err: true}
	}
	return r
}
func env(k, fallback string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return fallback
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "UNCONFIRMED: connection, permission, snapshot, or setup failed")
		os.Exit(1)
	}
}
func run() error {
	cfg := mysql.NewConfig()
	cfg.User = env("ISUCONP_DB_USER", "root")
	cfg.Passwd = os.Getenv("ISUCONP_DB_PASSWORD")
	cfg.Net = "tcp"
	cfg.Addr = env("ISUCONP_DB_HOST", "localhost") + ":" + env("ISUCONP_DB_PORT", "3306")
	cfg.DBName = env("ISUCONP_DB_NAME", "isuconp")
	cfg.Params = map[string]string{"charset": "utf8mb4"}
	db, err := sql.Open("mysql", cfg.FormatDSN())
	if err != nil {
		return err
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	conn, err := db.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()
	tx, err := conn.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelRepeatableRead, ReadOnly: true})
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var existing sql.NullInt64
	if err = tx.QueryRowContext(ctx, "SELECT MIN(id) FROM users").Scan(&existing); err != nil {
		return err
	}
	stmt, err := tx.PrepareContext(ctx, "SELECT * FROM users WHERE id = ?")
	if err != nil {
		return err
	}
	defer stmt.Close()
	cases := []struct {
		name string
		id   int64
	}{{"negative", -1}, {"zero", 0}, {"int_min", -2147483648}, {"int_max", 2147483647}, {"below_int_min", -2147483649}, {"above_int_max", 2147483648}}
	if existing.Valid {
		cases = append(cases, struct {
			name string
			id   int64
		}{"existing", existing.Int64})
	} else {
		fmt.Println("existing: UNCONFIRMED (no rows)")
	}
	// Find a guaranteed absent ID in the same snapshot, including the full INT domain case.
	var missing sql.NullInt64
	err = tx.QueryRowContext(ctx, "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM users WHERE id = -2147483648) THEN -2147483648 ELSE (SELECT MIN(CAST(u.id AS SIGNED) + 1) FROM users u LEFT JOIN users n ON n.id = CAST(u.id AS SIGNED) + 1 WHERE u.id < 2147483647 AND n.id IS NULL) END").Scan(&missing)
	if err != nil {
		return err
	}
	if missing.Valid {
		cases = append(cases, struct {
			name string
			id   int64
		}{"missing", missing.Int64})
	} else {
		fmt.Println("missing: UNCONFIRMED (no missing INT ID)")
	}
	failed := false
	for _, c := range cases {
		a := read(stmt.QueryContext(ctx, c.id))
		b := read(tx.QueryContext(ctx, "SELECT * FROM users WHERE id = "+strconv.FormatInt(c.id, 10)))
		status := "MATCH"
		if a.err || b.err {
			status = "ERROR/UNCONFIRMED"
			failed = true
		} else if !reflect.DeepEqual(a, b) {
			status = "MISMATCH"
			failed = true
		}
		fmt.Printf("%s: count=1 %s\n", c.name, status)
	}
	if failed || !existing.Valid || !missing.Valid {
		return fmt.Errorf("incomplete")
	}
	return nil
}
