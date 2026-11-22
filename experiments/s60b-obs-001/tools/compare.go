package main

import (
	"context"
	"database/sql"
	"encoding/json"
	_ "github.com/go-sql-driver/mysql"
	"log"
	"os"
	"reflect"
	"strconv"
	"time"
)

type result struct {
	Columns []string `json:"columns,omitempty"`
	Types   []string `json:"types,omitempty"`
	Values  []any    `json:"values,omitempty"`
	Missing bool     `json:"missing"`
	Error   string   `json:"error,omitempty"`
}

func query(ctx context.Context, tx *sql.Tx, q string, args ...any) (r result) {
	rows, err := tx.QueryContext(ctx, q, args...)
	if err != nil {
		r.Error = err.Error()
		return
	}
	defer rows.Close()
	r.Columns, err = rows.Columns()
	if err != nil {
		r.Error = err.Error()
		return
	}
	types, err := rows.ColumnTypes()
	if err != nil {
		r.Error = err.Error()
		return
	}
	for _, t := range types {
		r.Types = append(r.Types, t.DatabaseTypeName())
	}
	if !rows.Next() {
		if err = rows.Err(); err != nil {
			r.Error = err.Error()
		} else {
			r.Missing = true
		}
		return
	}
	values := make([]any, len(r.Columns))
	ptrs := make([]any, len(values))
	for i := range values {
		ptrs[i] = &values[i]
	}
	if err = rows.Scan(ptrs...); err != nil {
		r.Error = err.Error()
		return
	}
	for i, v := range values {
		if b, ok := v.([]byte); ok {
			values[i] = string(b)
		}
	}
	r.Values = values
	if rows.Next() {
		r.Error = "multiple rows"
	} else if err = rows.Err(); err != nil {
		r.Error = err.Error()
	}
	return
}
func main() {
	dsn := os.Getenv("OBS_MYSQL_DSN")
	if dsn == "" {
		log.Fatal("set OBS_MYSQL_DSN")
	}
	db, err := sql.Open("mysql", dsn)
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	tx, err := db.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelRepeatableRead, ReadOnly: true})
	if err != nil {
		log.Fatal(err)
	}
	defer tx.Rollback()
	var existing int64
	if err = tx.QueryRowContext(ctx, "SELECT id FROM users ORDER BY id LIMIT 1").Scan(&existing); err != nil {
		log.Fatal(err)
	}
	// Find a verified missing positive ID in the same snapshot, including when ID 1 is absent.
	var missing int64
	if err = tx.QueryRowContext(ctx, "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM users WHERE id = 1) THEN 1 ELSE (SELECT MIN(u.id + 1) FROM users u LEFT JOIN users v ON v.id = u.id + 1 WHERE v.id IS NULL AND u.id < 2147483647) END").Scan(&missing); err != nil {
		log.Fatal(err)
	}
	ids := []int64{existing, missing, 0, -1, -2147483648, 2147483647, -2147483649, 2147483648}
	enc := json.NewEncoder(os.Stdout)
	failed := false
	for _, id := range ids {
		s := strconv.FormatInt(id, 10)
		p := query(ctx, tx, "SELECT * FROM users WHERE id = ?", id)
		l := query(ctx, tx, "SELECT * FROM users WHERE id = "+s)
		equal := p.Error == "" && l.Error == "" && reflect.DeepEqual(p, l)
		if !equal {
			failed = true
		}
		if err = enc.Encode(map[string]any{"id": s, "prepared": p, "literal": l, "equal": equal}); err != nil {
			log.Fatal(err)
		}
	}
	if failed {
		os.Exit(1)
	}
}
