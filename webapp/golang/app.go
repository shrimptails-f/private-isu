package main

import (
	"context"
	crand "crypto/rand"
	"crypto/sha512"
	"database/sql"
	"fmt"
	"html/template"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/bradfitz/gomemcache/memcache"
	gsm "github.com/bradleypeabody/gorilla-sessions-memcache"
	"github.com/go-chi/chi/v5"
	mysql "github.com/go-sql-driver/mysql"
	"github.com/gorilla/sessions"
	"github.com/jmoiron/sqlx"
)

var (
	db                *tracedDB
	store             *gsm.MemcacheStore
	imageCacheState   = newImageCache(64 << 20)
	imageInitializeMu sync.RWMutex
)

const (
	postsPerPage  = 20
	ISO8601Format = "2006-01-02T15:04:05-07:00"
	UploadLimit   = 10 * 1024 * 1024 // 10mb
)

type User struct {
	ID          int       `db:"id"`
	AccountName string    `db:"account_name"`
	Passhash    string    `db:"passhash"`
	Authority   int       `db:"authority"`
	DelFlg      int       `db:"del_flg"`
	CreatedAt   time.Time `db:"created_at"`
}

type Post struct {
	ID           int       `db:"id"`
	UserID       int       `db:"user_id"`
	Imgdata      []byte    `db:"imgdata"`
	Body         string    `db:"body"`
	Mime         string    `db:"mime"`
	CreatedAt    time.Time `db:"created_at"`
	CommentCount int
	Comments     []Comment
	User         User
	CSRFToken    string
}

type Comment struct {
	ID        int       `db:"id"`
	PostID    int       `db:"post_id"`
	UserID    int       `db:"user_id"`
	Comment   string    `db:"comment"`
	CreatedAt time.Time `db:"created_at"`
	User      User
}

var memcacheClient *memcache.Client

func init() {
	memdAddr := os.Getenv("ISUCONP_MEMCACHED_ADDRESS")
	if memdAddr == "" {
		memdAddr = "localhost:11211"
	}
	memcacheClient = memcache.New(memdAddr)
	store = gsm.NewMemcacheStore(memcacheClient, "iscogram_", []byte("sendagaya"))
	log.SetFlags(log.Ldate | log.Ltime | log.Lshortfile)
}

func dbInitialize(ctx context.Context) {
	sqls := []string{
		"DELETE FROM users WHERE id > 1000",
		"DELETE FROM posts WHERE id > 10000",
		"DELETE FROM comments WHERE id > 100000",
		"UPDATE users SET del_flg = 0",
		"UPDATE users SET del_flg = 1 WHERE id % 50 = 0",
	}

	for _, sql := range sqls {
		db.ExecContext(ctx, sql)
	}
}

func tryLogin(ctx context.Context, accountName, password string) *User {
	u := User{}
	err := db.GetContext(ctx, &u, "SELECT * FROM users WHERE account_name = ? AND del_flg = 0", accountName)
	if err != nil {
		return nil
	}

	if calculatePasshash(ctx, u.AccountName, password) == u.Passhash {
		return &u
	} else {
		return nil
	}
}

func validateUser(accountName, password string) bool {
	return regexp.MustCompile(`\A[0-9a-zA-Z_]{3,}\z`).MatchString(accountName) &&
		regexp.MustCompile(`\A[0-9a-zA-Z_]{6,}\z`).MatchString(password)
}

func digest(_ context.Context, src string) string {
	sum := sha512.Sum512([]byte(src))
	return fmt.Sprintf("%x", sum)
}

func calculateSalt(ctx context.Context, accountName string) string {
	return digest(ctx, accountName)
}

func calculatePasshash(ctx context.Context, accountName, password string) string {
	return digest(ctx, password+":"+calculateSalt(ctx, accountName))
}

func getSession(r *http.Request) *sessions.Session {
	start := time.Now()
	session, err := store.Get(r, "isuconp-go.session")
	traceOperation(r.Context(), "session_get", "session_store_get", start, err)

	return session
}

func getSessionUser(r *http.Request) User {
	ctx := r.Context()
	session := getSession(r)
	uid, ok := session.Values["user_id"]
	if !ok || uid == nil {
		return User{}
	}

	u := User{}

	err := db.GetContext(ctx, &u, "SELECT * FROM `users` WHERE `id` = ?", uid)
	if err != nil {
		return User{}
	}

	return u
}

func getFlash(w http.ResponseWriter, r *http.Request, key string) string {
	session := getSession(r)
	value, ok := session.Values[key]

	if !ok || value == nil {
		return ""
	} else {
		delete(session.Values, key)
		session.Save(r, w)
		return value.(string)
	}
}

func makePosts(ctx context.Context, results []Post, csrfToken string, allComments bool) ([]Post, error) {
	// Find the first visible posts in their original order, including when
	// deleted accounts require looking beyond the first page.
	var posts []Post
	for start := 0; start < len(results) && len(posts) < postsPerPage; start += 32 {
		end := min(start+32, len(results))
		ids := make([]int, 0, end-start)
		for _, p := range results[start:end] {
			ids = append(ids, p.ID)
		}
		query, args, err := sqlx.In("SELECT u.* FROM users u INNER JOIN posts p ON p.user_id = u.id WHERE p.id IN (?)", ids)
		if err != nil {
			return nil, err
		}
		var users []User
		if err := db.SelectContext(ctx, &users, query, args...); err != nil {
			return nil, err
		}
		byID := make(map[int]User, len(users))
		for _, u := range users {
			byID[u.ID] = u
		}
		for _, p := range results[start:end] {
			u, ok := byID[p.UserID]
			if !ok {
				return nil, sql.ErrNoRows
			}
			if u.DelFlg != 0 {
				continue
			}
			p.User, p.CSRFToken = u, csrfToken
			posts = append(posts, p)
			if len(posts) == postsPerPage {
				break
			}
		}
	}
	if len(posts) == 0 {
		return posts, nil
	}

	ids := make([]int, 0, len(posts))
	for _, p := range posts {
		ids = append(ids, p.ID)
	}
	query, args, err := sqlx.In("SELECT post_id, COUNT(*) AS count FROM comments WHERE post_id IN (?) GROUP BY post_id", ids)
	if err != nil {
		return nil, err
	}
	var counts []struct {
		PostID int `db:"post_id"`
		Count  int `db:"count"`
	}
	if err := db.SelectContext(ctx, &counts, query, args...); err != nil {
		return nil, err
	}
	countsByPost := make(map[int]int, len(counts))
	for _, c := range counts {
		countsByPost[c.PostID] = c.Count
	}

	// Each subquery retains the original per-post ordering and LIMIT.
	parts := make([]string, 0, len(posts))
	commentArgs := make([]any, 0, len(posts))
	for _, p := range posts {
		q := "(SELECT * FROM comments WHERE post_id = ? ORDER BY created_at DESC"
		if !allComments {
			q += " LIMIT 3"
		}
		parts = append(parts, q+")")
		commentArgs = append(commentArgs, p.ID)
	}
	var comments []Comment
	if err := db.SelectContext(ctx, &comments, strings.Join(parts, " UNION ALL ")+" ORDER BY post_id, created_at DESC", commentArgs...); err != nil {
		return nil, err
	}
	userIDs := make([]int, 0, len(comments))
	for _, c := range comments {
		userIDs = append(userIDs, c.UserID)
	}
	usersByID := make(map[int]User)
	if len(userIDs) > 0 {
		query, args, err := sqlx.In("SELECT * FROM users WHERE id IN (?)", userIDs)
		if err != nil {
			return nil, err
		}
		var users []User
		if err := db.SelectContext(ctx, &users, query, args...); err != nil {
			return nil, err
		}
		for _, u := range users {
			usersByID[u.ID] = u
		}
	}
	commentsByPost := make(map[int][]Comment)
	for _, c := range comments {
		u, ok := usersByID[c.UserID]
		if !ok {
			return nil, sql.ErrNoRows
		}
		c.User = u
		commentsByPost[c.PostID] = append(commentsByPost[c.PostID], c)
	}
	for i := range posts {
		posts[i].CommentCount = countsByPost[posts[i].ID]
		comments := commentsByPost[posts[i].ID]
		for l, r := 0, len(comments)-1; l < r; l, r = l+1, r-1 {
			comments[l], comments[r] = comments[r], comments[l]
		}
		posts[i].Comments = comments
	}
	return posts, nil
}

func imageURL(p Post) string {
	ext := ""
	if p.Mime == "image/jpeg" {
		ext = ".jpg"
	} else if p.Mime == "image/png" {
		ext = ".png"
	} else if p.Mime == "image/gif" {
		ext = ".gif"
	}

	return "/image/" + strconv.Itoa(p.ID) + ext
}

func isLogin(u User) bool {
	return u.ID != 0
}

func getCSRFToken(r *http.Request) string {
	session := getSession(r)
	csrfToken, ok := session.Values["csrf_token"]
	if !ok {
		return ""
	}
	return csrfToken.(string)
}

func secureRandomStr(b int) string {
	k := make([]byte, b)
	if _, err := crand.Read(k); err != nil {
		panic(err)
	}
	return fmt.Sprintf("%x", k)
}

func getTemplPath(filename string) string {
	return path.Join("templates", filename)
}

func getInitialize(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	imageInitializeMu.Lock()
	dbInitialize(ctx)
	imageCacheState.clear()
	imageInitializeMu.Unlock()
	w.WriteHeader(http.StatusOK)
}

func getLogin(w http.ResponseWriter, r *http.Request) {
	me := getSessionUser(r)

	if isLogin(me) {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}

	template.Must(template.ParseFiles(
		getTemplPath("layout.html"),
		getTemplPath("login.html")),
	).Execute(w, struct {
		Me    User
		Flash string
	}{me, getFlash(w, r, "notice")})
}

func postLogin(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	if isLogin(getSessionUser(r)) {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}

	u := tryLogin(ctx, r.FormValue("account_name"), r.FormValue("password"))

	if u != nil {
		session := getSession(r)
		session.Values["user_id"] = u.ID
		session.Values["csrf_token"] = secureRandomStr(16)
		session.Save(r, w)

		http.Redirect(w, r, "/", http.StatusFound)
	} else {
		session := getSession(r)
		session.Values["notice"] = "アカウント名かパスワードが間違っています"
		session.Save(r, w)

		http.Redirect(w, r, "/login", http.StatusFound)
	}
}

func getRegister(w http.ResponseWriter, r *http.Request) {
	if isLogin(getSessionUser(r)) {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}

	template.Must(template.ParseFiles(
		getTemplPath("layout.html"),
		getTemplPath("register.html")),
	).Execute(w, struct {
		Me    User
		Flash string
	}{User{}, getFlash(w, r, "notice")})
}

func postRegister(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	if isLogin(getSessionUser(r)) {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}

	accountName, password := r.FormValue("account_name"), r.FormValue("password")

	validated := validateUser(accountName, password)
	if !validated {
		session := getSession(r)
		session.Values["notice"] = "アカウント名は3文字以上、パスワードは6文字以上である必要があります"
		session.Save(r, w)

		http.Redirect(w, r, "/register", http.StatusFound)
		return
	}

	exists := 0
	// ユーザーが存在しない場合はエラーになるのでエラーチェックはしない
	db.GetContext(ctx, &exists, "SELECT 1 FROM users WHERE `account_name` = ?", accountName)

	if exists == 1 {
		session := getSession(r)
		session.Values["notice"] = "アカウント名がすでに使われています"
		session.Save(r, w)

		http.Redirect(w, r, "/register", http.StatusFound)
		return
	}

	query := "INSERT INTO `users` (`account_name`, `passhash`) VALUES (?,?)"
	result, err := db.ExecContext(ctx, query, accountName, calculatePasshash(ctx, accountName, password))
	if err != nil {
		log.Print(err)
		return
	}

	session := getSession(r)
	uid, err := result.LastInsertId()
	if err != nil {
		log.Print(err)
		return
	}
	session.Values["user_id"] = uid
	session.Values["csrf_token"] = secureRandomStr(16)
	session.Save(r, w)

	http.Redirect(w, r, "/", http.StatusFound)
}

func getLogout(w http.ResponseWriter, r *http.Request) {
	session := getSession(r)
	delete(session.Values, "user_id")
	session.Options = &sessions.Options{MaxAge: -1}
	session.Save(r, w)

	http.Redirect(w, r, "/", http.StatusFound)
}

// listPosts keeps the original query as a fallback when equal timestamps make
// its otherwise unspecified tie ordering observable.
func listPosts(ctx context.Context, maxCreatedAt *string) ([]Post, error) {
	results := []Post{}
	where := ""
	var args []any
	if maxCreatedAt != nil {
		where = "p.created_at <= ? AND "
		args = append(args, *maxCreatedAt)
	}
	query := "SELECT p.id, p.user_id, p.body, p.mime, p.created_at FROM posts p LEFT JOIN users u ON u.id = p.user_id WHERE " + where + "(u.id IS NULL OR u.del_flg = 0) ORDER BY p.created_at DESC LIMIT 20"
	if err := db.SelectContext(ctx, &results, query, args...); err != nil {
		return nil, err
	}
	if len(results) == 0 {
		return results, nil
	}

	// Include deleted users in this check: they participated in the original
	// sort before makePosts filtered them out.
	tieQuery := "SELECT COUNT(*) - COUNT(DISTINCT created_at) FROM posts WHERE created_at >= ?"
	tieArgs := []any{results[len(results)-1].CreatedAt}
	if maxCreatedAt != nil {
		tieQuery += " AND created_at <= ?"
		tieArgs = append(tieArgs, *maxCreatedAt)
	}
	var ties int
	if err := db.GetContext(ctx, &ties, tieQuery, tieArgs...); err != nil {
		return nil, err
	}
	if ties == 0 {
		return results, nil
	}

	results = []Post{}
	query = "SELECT `id`, `user_id`, `body`, `mime`, `created_at` FROM `posts` IGNORE INDEX (idx_posts_created_at)"
	if maxCreatedAt != nil {
		query += " WHERE `created_at` <= ?"
	}
	query += " ORDER BY `created_at` DESC"
	if err := db.SelectContext(ctx, &results, query, args...); err != nil {
		return nil, err
	}
	return results, nil
}

func getIndex(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	me := getSessionUser(r)

	results, err := listPosts(ctx, nil)
	if err != nil {
		log.Print(err)
		return
	}

	posts, err := makePosts(ctx, results, getCSRFToken(r), false)
	if err != nil {
		log.Print(err)
		return
	}

	fmap := template.FuncMap{
		"imageURL": imageURL,
	}

	template.Must(template.New("layout.html").Funcs(fmap).ParseFiles(
		getTemplPath("layout.html"),
		getTemplPath("index.html"),
		getTemplPath("posts.html"),
		getTemplPath("post.html"),
	)).Execute(w, struct {
		Posts     []Post
		Me        User
		CSRFToken string
		Flash     string
	}{posts, me, getCSRFToken(r), getFlash(w, r, "notice")})
}

func getAccountName(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	accountName := r.PathValue("accountName")
	user := User{}

	err := db.GetContext(ctx, &user, "SELECT * FROM `users` WHERE `account_name` = ? AND `del_flg` = 0", accountName)
	if err != nil {
		log.Print(err)
		return
	}

	if user.ID == 0 {
		w.WriteHeader(http.StatusNotFound)
		return
	}

	results := []Post{}

	err = db.SelectContext(ctx, &results, "SELECT `id`, `user_id`, `body`, `mime`, `created_at` FROM `posts` WHERE `user_id` = ? ORDER BY `created_at` DESC", user.ID)
	if err != nil {
		log.Print(err)
		return
	}

	posts, err := makePosts(ctx, results, getCSRFToken(r), false)
	if err != nil {
		log.Print(err)
		return
	}

	commentCount := 0
	err = db.GetContext(ctx, &commentCount, "SELECT COUNT(*) AS count FROM `comments` WHERE `user_id` = ?", user.ID)
	if err != nil {
		log.Print(err)
		return
	}

	postIDs := []int{}
	err = db.SelectContext(ctx, &postIDs, "SELECT `id` FROM `posts` WHERE `user_id` = ?", user.ID)
	if err != nil {
		log.Print(err)
		return
	}
	postCount := len(postIDs)

	commentedCount := 0
	if postCount > 0 {
		s := []string{}
		for range postIDs {
			s = append(s, "?")
		}
		placeholder := strings.Join(s, ", ")

		// convert []int -> []any
		args := make([]any, len(postIDs))
		for i, v := range postIDs {
			args[i] = v
		}

		err = db.GetContext(ctx, &commentedCount, "SELECT COUNT(*) AS count FROM `comments` WHERE `post_id` IN ("+placeholder+")", args...)
		if err != nil {
			log.Print(err)
			return
		}
	}

	me := getSessionUser(r)

	fmap := template.FuncMap{
		"imageURL": imageURL,
	}

	template.Must(template.New("layout.html").Funcs(fmap).ParseFiles(
		getTemplPath("layout.html"),
		getTemplPath("user.html"),
		getTemplPath("posts.html"),
		getTemplPath("post.html"),
	)).Execute(w, struct {
		Posts          []Post
		User           User
		PostCount      int
		CommentCount   int
		CommentedCount int
		Me             User
	}{posts, user, postCount, commentCount, commentedCount, me})
}

func getPosts(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	m, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		log.Print(err)
		return
	}
	maxCreatedAt := m.Get("max_created_at")
	if maxCreatedAt == "" {
		return
	}

	t, err := time.Parse(ISO8601Format, maxCreatedAt)
	if err != nil {
		log.Print(err)
		return
	}

	maxTime := t.Format(ISO8601Format)
	results, err := listPosts(ctx, &maxTime)
	if err != nil {
		log.Print(err)
		return
	}

	posts, err := makePosts(ctx, results, getCSRFToken(r), false)
	if err != nil {
		log.Print(err)
		return
	}

	if len(posts) == 0 {
		w.WriteHeader(http.StatusNotFound)
		return
	}

	fmap := template.FuncMap{
		"imageURL": imageURL,
	}

	template.Must(template.New("posts.html").Funcs(fmap).ParseFiles(
		getTemplPath("posts.html"),
		getTemplPath("post.html"),
	)).Execute(w, posts)
}

func getPostsID(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	pidStr := r.PathValue("id")
	pid, err := strconv.Atoi(pidStr)
	if err != nil {
		w.WriteHeader(http.StatusNotFound)
		return
	}

	results := []Post{}
	err = db.SelectContext(ctx, &results, "SELECT * FROM `posts` WHERE `id` = ?", pid)
	if err != nil {
		log.Print(err)
		return
	}

	posts, err := makePosts(ctx, results, getCSRFToken(r), true)
	if err != nil {
		log.Print(err)
		return
	}

	if len(posts) == 0 {
		w.WriteHeader(http.StatusNotFound)
		return
	}

	p := posts[0]

	me := getSessionUser(r)

	fmap := template.FuncMap{
		"imageURL": imageURL,
	}

	template.Must(template.New("layout.html").Funcs(fmap).ParseFiles(
		getTemplPath("layout.html"),
		getTemplPath("post_id.html"),
		getTemplPath("post.html"),
	)).Execute(w, struct {
		Post Post
		Me   User
	}{p, me})
}

func postIndex(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	me := getSessionUser(r)
	if !isLogin(me) {
		http.Redirect(w, r, "/login", http.StatusFound)
		return
	}

	if r.FormValue("csrf_token") != getCSRFToken(r) {
		w.WriteHeader(http.StatusUnprocessableEntity)
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		session := getSession(r)
		session.Values["notice"] = "画像が必須です"
		session.Save(r, w)

		http.Redirect(w, r, "/", http.StatusFound)
		return
	}

	mime := ""
	if file != nil {
		// 投稿のContent-Typeからファイルのタイプを決定する
		contentType := header.Header["Content-Type"][0]
		if strings.Contains(contentType, "jpeg") {
			mime = "image/jpeg"
		} else if strings.Contains(contentType, "png") {
			mime = "image/png"
		} else if strings.Contains(contentType, "gif") {
			mime = "image/gif"
		} else {
			session := getSession(r)
			session.Values["notice"] = "投稿できる画像形式はjpgとpngとgifだけです"
			session.Save(r, w)

			http.Redirect(w, r, "/", http.StatusFound)
			return
		}
	}

	readStart := time.Now()
	filedata, err := io.ReadAll(file)
	traceOperation(ctx, "file_read", "upload", readStart, err)
	if err != nil {
		log.Print(err)
		return
	}

	if len(filedata) > UploadLimit {
		session := getSession(r)
		session.Values["notice"] = "ファイルサイズが大きすぎます"
		session.Save(r, w)

		http.Redirect(w, r, "/", http.StatusFound)
		return
	}

	query := "INSERT INTO `posts` (`user_id`, `mime`, `imgdata`, `body`) VALUES (?,?,?,?)"
	result, err := db.ExecContext(
		ctx,
		query,
		me.ID,
		mime,
		filedata,
		r.FormValue("body"),
	)
	if err != nil {
		log.Print(err)
		return
	}

	pid, err := result.LastInsertId()
	if err != nil {
		log.Print(err)
		return
	}

	http.Redirect(w, r, "/posts/"+strconv.FormatInt(pid, 10), http.StatusFound)
}

func getImage(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	pidStr := r.PathValue("id")
	pid, err := strconv.Atoi(pidStr)
	if err != nil {
		w.WriteHeader(http.StatusNotFound)
		return
	}

	imageInitializeMu.RLock()
	start := time.Now()
	image, found := imageCacheState.get(pid)
	cacheResult := "hit"
	if !found {
		cacheResult = "miss"
	}
	traceOperation(ctx, "image_cache", cacheResult, start, nil)
	if !found {
		post := Post{}
		err = db.GetContext(ctx, &post, "SELECT * FROM `posts` WHERE `id` = ?", pid)
		if err != nil {
			imageInitializeMu.RUnlock()
			log.Print(err)
			return
		}
		image = cachedImage{id: pid, mime: post.Mime, data: post.Imgdata}
		imageCacheState.put(image)
	}
	imageInitializeMu.RUnlock()

	ext := r.PathValue("ext")

	if ext == "jpg" && image.mime == "image/jpeg" ||
		ext == "png" && image.mime == "image/png" ||
		ext == "gif" && image.mime == "image/gif" {
		w.Header().Set("Content-Type", image.mime)
		_, err := w.Write(image.data)
		if err != nil {
			log.Print(err)
			return
		}
		return
	}

	w.WriteHeader(http.StatusNotFound)
}

func postComment(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	me := getSessionUser(r)
	if !isLogin(me) {
		http.Redirect(w, r, "/login", http.StatusFound)
		return
	}

	if r.FormValue("csrf_token") != getCSRFToken(r) {
		w.WriteHeader(http.StatusUnprocessableEntity)
		return
	}

	postID, err := strconv.Atoi(r.FormValue("post_id"))
	if err != nil {
		log.Print("post_idは整数のみです")
		return
	}

	query := "INSERT INTO `comments` (`post_id`, `user_id`, `comment`) VALUES (?,?,?)"
	_, err = db.ExecContext(ctx, query, postID, me.ID, r.FormValue("comment"))
	if err != nil {
		log.Print(err)
		return
	}

	http.Redirect(w, r, fmt.Sprintf("/posts/%d", postID), http.StatusFound)
}

func getAdminBanned(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	me := getSessionUser(r)
	if !isLogin(me) {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}

	if me.Authority == 0 {
		w.WriteHeader(http.StatusForbidden)
		return
	}

	users := []User{}
	err := db.SelectContext(ctx, &users, "SELECT * FROM `users` WHERE `authority` = 0 AND `del_flg` = 0 ORDER BY `created_at` DESC")
	if err != nil {
		log.Print(err)
		return
	}

	template.Must(template.ParseFiles(
		getTemplPath("layout.html"),
		getTemplPath("banned.html")),
	).Execute(w, struct {
		Users     []User
		Me        User
		CSRFToken string
	}{users, me, getCSRFToken(r)})
}

func postAdminBanned(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	me := getSessionUser(r)
	if !isLogin(me) {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}

	if me.Authority == 0 {
		w.WriteHeader(http.StatusForbidden)
		return
	}

	if r.FormValue("csrf_token") != getCSRFToken(r) {
		w.WriteHeader(http.StatusUnprocessableEntity)
		return
	}

	query := "UPDATE `users` SET `del_flg` = ? WHERE `id` = ?"

	err := r.ParseForm()
	if err != nil {
		log.Print(err)
		return
	}

	for _, id := range r.Form["uid[]"] {
		db.ExecContext(ctx, query, 1, id)
	}

	http.Redirect(w, r, "/admin/banned", http.StatusFound)
}

func main() {
	host := os.Getenv("ISUCONP_DB_HOST")
	if host == "" {
		host = "localhost"
	}
	port := os.Getenv("ISUCONP_DB_PORT")
	if port == "" {
		port = "3306"
	}
	_, err := strconv.Atoi(port)
	if err != nil {
		log.Fatalf("Failed to read DB port number from an environment variable ISUCONP_DB_PORT.\nError: %s", err.Error())
	}
	user := os.Getenv("ISUCONP_DB_USER")
	if user == "" {
		user = "root"
	}
	password := os.Getenv("ISUCONP_DB_PASSWORD")
	dbname := os.Getenv("ISUCONP_DB_NAME")
	if dbname == "" {
		dbname = "isuconp"
	}

	cfg := mysql.NewConfig()
	cfg.User = user
	cfg.Passwd = password
	cfg.Net = "tcp"
	cfg.Addr = fmt.Sprintf("%s:%s", host, port)
	cfg.DBName = dbname
	cfg.Params = map[string]string{
		"charset": "utf8mb4",
	}
	cfg.ParseTime = true
	cfg.Loc = time.Local
	dsn := cfg.FormatDSN()

	connection, err := sqlx.Open("mysql", dsn)
	if err != nil {
		log.Fatalf("Failed to connect to DB: %s.", err.Error())
	}
	db = &tracedDB{DB: connection}
	defer db.Close()

	r := chi.NewRouter()
	r.Use(traceRequest)

	r.Get("/initialize", getInitialize)
	r.Get("/login", getLogin)
	r.Post("/login", postLogin)
	r.Get("/register", getRegister)
	r.Post("/register", postRegister)
	r.Get("/logout", getLogout)
	r.Get("/", getIndex)
	r.Get("/posts", getPosts)
	r.Get("/posts/{id}", getPostsID)
	r.Post("/", postIndex)
	r.Get("/image/{id}.{ext}", getImage)
	r.Post("/comment", postComment)
	r.Get("/admin/banned", getAdminBanned)
	r.Post("/admin/banned", postAdminBanned)
	r.Get(`/@{accountName:[0-9a-zA-Z_]+}`, getAccountName)
	r.Mount("/", http.FileServer(http.Dir("../public")))

	log.Fatal(http.ListenAndServe(":8080", r))
}
