import os, sqlite3
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'mongol_amt.db')
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-change-this-secret-before-deploy')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

STATUSES = ['Шинэ', 'Баталгаажсан', 'Бэлтгэж байна', 'Бэлэн болсон', 'Хүргэлтэд гарсан', 'Хүргэгдсэн']
ROLES = {'customer':'Үйлчлүүлэгч','staff':'Хоолны газрын ажилтан','courier':'Хүргэлтийн ажилтан','admin':'Администратор'}

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con

def init_db():
    with db() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, phone TEXT DEFAULT '', password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('customer','staff','courier','admin')));
        CREATE TABLE IF NOT EXISTS foods (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT NOT NULL, price INTEGER NOT NULL CHECK(price>=0), category TEXT NOT NULL, emoji TEXT NOT NULL, available INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL REFERENCES users(id), recipient TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL, note TEXT DEFAULT '', delivery INTEGER NOT NULL, total INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Шинэ', courier_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS order_items (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id), food_id INTEGER REFERENCES foods(id), food_name TEXT NOT NULL, price INTEGER NOT NULL, quantity INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS order_events (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id), status TEXT NOT NULL, created_at TEXT NOT NULL);
        ''')
        con.execute('''CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id), method TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', reference TEXT NOT NULL, created_at TEXT NOT NULL)''')
        cols={r['name'] for r in con.execute('PRAGMA table_info(users)')}
        if 'active' not in cols:
            con.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
        if 'last_login' not in cols:
            con.execute('ALTER TABLE users ADD COLUMN last_login TEXT')
        if not con.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            for name,email,role in [('Админ','admin@demo.mn','admin'),('Гал тогооны ажилтан','staff@demo.mn','staff'),('Хүргэлтийн ажилтан','courier@demo.mn','courier')]:
                con.execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)', (name,email,generate_password_hash('Demo12345!'),role))
        if not con.execute('SELECT 1 FROM foods LIMIT 1').fetchone():
            foods=[('Бууз','Шинэхэн жигнэсэн махтай бууз, 6 ширхэг',12000,'Үндсэн хоол','🥟'),('Хуушуур','Шаржигнуур хуушуур, 3 ширхэг',10500,'Үндсэн хоол','🥟'),('Цуйван','Үхрийн мах, ногоотой гарын гурилтай цуйван',15000,'Үндсэн хоол','🍜'),('Гурилтай шөл','Үхрийн махтай халуун шөл',13000,'Шөл','🍲'),('Банштай цай','Сүүтэй цай, жижиг банштай',9500,'Шөл','🥣'),('Хорхог','Мах, төмс, луувантай хорхог',29000,'Үндсэн хоол','🍖'),('Аарцтай ундаа','Уламжлалт аарцтай ундаа',4500,'Ундаа','🥛'),('Сүүтэй цай','Монгол сүүтэй цай',3000,'Ундаа','🍵')]
            con.executemany('INSERT INTO foods(name,description,price,category,emoji) VALUES(?,?,?,?,?)',foods)

# Зөвхөн тухайн хоол мөн гэдэг нь эх сурвалжаар батлагдсан зургууд.
# Wikimedia Commons: https://commons.wikimedia.org/ (зураг тус бүрийн лицензийг README-д харна уу)
FOOD_PHOTOS = {'Бууз': 'Buuz.jpg', 'Хуушуур': 'MongolianKhuushuur.JPG', 'Цуйван': 'Tsuivan.jpg', 'Гурилтай шөл': 'Guriltai shul.jpg', 'Хорхог': 'Khorkhog.jpg', 'Сүүтэй цай': 'Suutei tsai.jpg'}

def food_photo(name):
    from urllib.parse import quote
    filename = FOOD_PHOTOS.get(name)
    return 'https://commons.wikimedia.org/wiki/Special:FilePath/' + quote(filename) + '?width=850' if filename else None

@app.context_processor
def context():
    return dict(food_photo=food_photo, roles=ROLES, statuses=STATUSES, cart_count=sum(session.get('cart',{}).values()))

def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def inner(*args,**kwargs):
            if not session.get('uid'):
                flash('Эхлээд нэвтэрнэ үү.', 'error'); return redirect(url_for('login'))
            with db() as con:
                account=con.execute('SELECT role,active FROM users WHERE id=?',(session['uid'],)).fetchone()
            if not account or not account['active']:
                session.clear(); flash('Таны бүртгэл идэвхгүй байна.','error'); return redirect(url_for('login'))
            session['role']=account['role']
            if role and session.get('role') != role:
                abort(403)
            return fn(*args,**kwargs)
        return inner
    return decorator

def current_user():
    with db() as con:
        return con.execute('SELECT * FROM users WHERE id=?',(session['uid'],)).fetchone()

@app.route('/')
def home():
    with db() as con: foods=con.execute('SELECT * FROM foods WHERE available=1 ORDER BY id').fetchall()
    return render_template('home.html',foods=foods)

@app.route('/register',methods=['GET','POST'])
def register():
    if request.method=='POST':
        name=request.form.get('name','').strip(); email=request.form.get('email','').strip().lower(); phone=request.form.get('phone','').strip(); password=request.form.get('password',''); confirm=request.form.get('confirm_password','')
        if password != confirm:
            flash('Нууц үг давтан оруулсан утгатай таарахгүй байна.','error')
            return render_template('auth.html',mode='register')
        if not name or '@' not in email or len(password)<8:
            flash('Нэр, зөв имэйл болон 8-аас дээш тэмдэгттэй нууц үг оруулна уу.','error')
        else:
            try:
                with db() as con:
                    con.execute('INSERT INTO users(name,email,phone,password_hash,role) VALUES(?,?,?,?,?)',(name,email,phone,generate_password_hash(password),'customer'))
                flash('Бүртгэл үүслээ. Нэвтэрнэ үү.','success'); return redirect(url_for('login'))
            except sqlite3.IntegrityError: flash('Энэ имэйл бүртгэлтэй байна.','error')
    return render_template('auth.html',mode='register')

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        email=request.form.get('email','').strip().lower(); password=request.form.get('password','')
        with db() as con: user=con.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        if user and user['active'] and check_password_hash(user['password_hash'],password):
            with db() as con: con.execute('UPDATE users SET last_login=? WHERE id=?',(datetime.now().strftime('%Y-%m-%d %H:%M'),user['id']))
            session.clear(); session['uid']=user['id']; session['name']=user['name']; session['role']=user['role']
            return redirect(url_for('dashboard'))
        flash('Имэйл эсвэл нууц үг буруу байна.','error')
    return render_template('auth.html',mode='login')

@app.post('/logout')
def logout():
    session.clear(); flash('Системээс гарлаа.','success'); return redirect(url_for('home'))

@app.post('/cart/add/<int:food_id>')
@login_required('customer')
def cart_add(food_id):
    with db() as con: food=con.execute('SELECT * FROM foods WHERE id=? AND available=1',(food_id,)).fetchone()
    if not food: abort(404)
    cart=session.get('cart',{}); key=str(food_id); cart[key]=min(cart.get(key,0)+1,99); session['cart']=cart
    flash('Сагсанд нэмлээ.','success'); return redirect(url_for('home'))

def cart_details():
    cart=session.get('cart',{}); items=[]; total=0
    with db() as con:
        for key,qty in cart.items():
            if not str(key).isdigit() or not isinstance(qty,int) or qty<1: continue
            food=con.execute('SELECT * FROM foods WHERE id=? AND available=1',(int(key),)).fetchone()
            if food:
                items.append({'food':food,'qty':qty,'subtotal':food['price']*qty}); total+=food['price']*qty
    return items,total

@app.route('/cart')
@login_required('customer')
def cart():
    items,total=cart_details(); return render_template('cart.html',items=items,total=total)

@app.post('/cart/update/<int:food_id>')
@login_required('customer')
def cart_update(food_id):
    qty=max(0,min(99,int(request.form.get('qty','0')) if request.form.get('qty','0').lstrip('-').isdigit() else 0))
    cart=session.get('cart',{}); key=str(food_id)
    if qty: cart[key]=qty
    else: cart.pop(key,None)
    session['cart']=cart; return redirect(url_for('cart'))

@app.route('/checkout',methods=['GET','POST'])
@login_required('customer')
def checkout():
    items,subtotal=cart_details()
    if not items: flash('Сагс хоосон байна.','error'); return redirect(url_for('home'))
    user=current_user()
    if request.method=='POST':
        payment_method=request.form.get('payment_method','cash'); recipient=request.form.get('recipient','').strip(); phone=request.form.get('phone','').strip(); address=request.form.get('address','').strip(); note=request.form.get('note','').strip(); delivery=1 if request.form.get('delivery')=='1' else 0
        if payment_method not in ('cash','qpay','card'): abort(400)
        if not recipient or not phone or (delivery and not address): flash('Нэр, утас, хүргэлт сонгосон бол хаягаа оруулна уу.','error')
        else:
            fee=4000 if delivery else 0; now=datetime.now().strftime('%Y-%m-%d %H:%M')
            with db() as con:
                cur=con.execute('INSERT INTO orders(customer_id,recipient,phone,address,note,delivery,total,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(user['id'],recipient,phone,address if delivery else 'Өөрөө ирж авах',note,delivery,subtotal+fee,'Шинэ',now))
                oid=cur.lastrowid
                for item in items:
                    f=item['food']; con.execute('INSERT INTO order_items(order_id,food_id,food_name,price,quantity) VALUES(?,?,?,?,?)',(oid,f['id'],f['name'],f['price'],item['qty']))
                import secrets
                con.execute('INSERT INTO payments(order_id,method,status,reference,created_at) VALUES(?,?,?,?,?)',(oid,payment_method,'pending' if payment_method!='cash' else 'pay_on_delivery',secrets.token_hex(12),now))
                if payment_method=='cash':
                    con.execute('INSERT INTO order_events(order_id,status,created_at) VALUES(?,?,?)',(oid,'Шинэ',now))
            session['cart']={}
            if payment_method!='cash': return redirect(url_for('payment_demo',order_id=oid))
            flash('Захиалга бүртгэгдлээ. Төлбөрийг хүлээн авах үед төлнө.','success'); return redirect(url_for('order_detail',order_id=oid))
    return render_template('checkout.html',items=items,subtotal=subtotal,user=user)

@app.route('/payment/demo/<int:order_id>', methods=['GET','POST'])
@login_required('customer')
def payment_demo(order_id):
    with db() as con:
        order=con.execute('SELECT * FROM orders WHERE id=? AND customer_id=?',(order_id,session['uid'])).fetchone()
        if not order: abort(404)
        payment=con.execute('SELECT * FROM payments WHERE order_id=?',(order_id,)).fetchone()
        if not payment or payment['method']=='cash': abort(404)
        if request.method=='POST' and payment['status']=='pending':
            # DEMO ONLY: this does not charge a card or verify a QPay transaction.
            if request.form.get('action')=='simulate_success':
                con.execute('UPDATE payments SET status=? WHERE order_id=?',('demo_paid',order_id))
                con.execute('INSERT INTO order_events(order_id,status,created_at) VALUES(?,?,?)',(order_id,'Шинэ',datetime.now().strftime('%Y-%m-%d %H:%M')))
                flash('ТУРШИЛТ: Төлбөр амжилттай гэж тэмдэглэлээ. Бодит мөнгө шилжээгүй.','success')
                return redirect(url_for('order_detail',order_id=order_id))
        return render_template('payment_demo.html',order=order,payment=payment)

@app.route('/orders')
@login_required('customer')
def orders():
    with db() as con: rows=con.execute('SELECT * FROM orders WHERE customer_id=? ORDER BY id DESC',(session['uid'],)).fetchall()
    return render_template('orders.html',orders=rows,title='Миний захиалгууд')

@app.route('/orders/<int:order_id>')
@login_required()
def order_detail(order_id):
    with db() as con:
        order=con.execute('SELECT o.*,u.name AS courier_name FROM orders o LEFT JOIN users u ON o.courier_id=u.id WHERE o.id=?',(order_id,)).fetchone()
        if not order: abort(404)
        role=session['role']
        if role=='customer' and order['customer_id']!=session['uid']: abort(403)
        if role=='courier' and order['courier_id']!=session['uid']: abort(403)
        items=con.execute('SELECT * FROM order_items WHERE order_id=?',(order_id,)).fetchall()
        events=con.execute('SELECT * FROM order_events WHERE order_id=? ORDER BY id',(order_id,)).fetchall()
        payment=con.execute('SELECT * FROM payments WHERE order_id=?',(order_id,)).fetchone()
    return render_template('detail.html',order=order,items=items,events=events,payment=payment)

@app.route('/dashboard')
@login_required()
def dashboard():
    role=session['role']
    if role=='customer': return redirect(url_for('orders'))
    with db() as con:
        if role=='courier': rows=con.execute("SELECT * FROM orders WHERE delivery=1 AND (courier_id=? OR (courier_id IS NULL AND status='Бэлэн болсон')) ORDER BY id DESC",(session['uid'],)).fetchall()
        else: rows=con.execute("SELECT o.* FROM orders o LEFT JOIN payments p ON p.order_id=o.id WHERE p.id IS NULL OR p.status IN ('pay_on_delivery','demo_paid') ORDER BY o.id DESC").fetchall()
        couriers=con.execute("SELECT id,name FROM users WHERE role='courier' ORDER BY name").fetchall() if role=='admin' else []
        foods=con.execute('SELECT * FROM foods ORDER BY id DESC').fetchall() if role=='admin' else []
        order_items={}
        if rows:
            ids=[o['id'] for o in rows]
            placeholders=','.join('?' for _ in ids)
            for item in con.execute(f'SELECT * FROM order_items WHERE order_id IN ({placeholders}) ORDER BY id',ids):
                order_items.setdefault(item['order_id'],[]).append(item)
        courier_names={u['id']:u['name'] for u in con.execute("SELECT id,name FROM users WHERE role='courier'")}
        role_counts={r['role']:r['n'] for r in con.execute('SELECT role,COUNT(*) n FROM users GROUP BY role')} if role=='admin' else {}
    return render_template('dashboard.html',orders=rows,couriers=couriers,foods=foods,order_items=order_items,courier_names=courier_names,role_counts=role_counts)

def change_status(order_id,new_status):
    with db() as con:
        order=con.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
        if not order: abort(404)
        pay=con.execute('SELECT method,status FROM payments WHERE order_id=?',(order_id,)).fetchone()
        if pay and pay['method']!='cash' and pay['status']!='demo_paid': abort(403)
        role=session['role']; old=order['status']
        if role=='staff':
            allowed={'Шинэ':'Баталгаажсан','Баталгаажсан':'Бэлтгэж байна','Бэлтгэж байна':'Бэлэн болсон'}
            if allowed.get(old)!=new_status: abort(403)
        elif role=='courier':
            if not order['delivery'] or (order['courier_id'] not in (None,session['uid'])): abort(403)
            if order['courier_id'] is None and old!='Бэлэн болсон': abort(403)
            if (old,new_status) not in [('Бэлэн болсон','Хүргэлтэд гарсан'),('Хүргэлтэд гарсан','Хүргэгдсэн')]: abort(403)
        elif role=='admin':
            if new_status not in STATUSES or STATUSES.index(new_status)<STATUSES.index(old): abort(400)
        else: abort(403)
        if not order['delivery'] and new_status=='Хүргэлтэд гарсан': abort(400)
        if new_status=='Бэлэн болсон' and order['delivery'] and not order['courier_id']:
            # Хоол бэлэн болоход хамгийн цөөн идэвхтэй хүргэлттэй ажилтанд автоматаар хуваарилна.
            courier=con.execute("""SELECT u.id FROM users u
                LEFT JOIN orders o ON o.courier_id=u.id AND o.delivery=1
                    AND o.status IN ('Бэлэн болсон','Хүргэлтэд гарсан')
                WHERE u.role='courier' AND u.active=1
                GROUP BY u.id ORDER BY COUNT(o.id),u.id LIMIT 1""").fetchone()
            if courier: con.execute('UPDATE orders SET courier_id=? WHERE id=?',(courier['id'],order_id))
        if new_status=='Хүргэлтэд гарсан' and not order['courier_id']:
            if role=='courier':
                # Хуваарилагдаагүй бэлэн захиалгыг ажилтан өөрөө авч болно.
                con.execute('UPDATE orders SET courier_id=? WHERE id=? AND courier_id IS NULL',(session['uid'],order_id))
                owner=con.execute('SELECT courier_id FROM orders WHERE id=?',(order_id,)).fetchone()
                if owner['courier_id']!=session['uid']: abort(403)
            else: abort(400)
        con.execute('UPDATE orders SET status=? WHERE id=?',(new_status,order_id))
        con.execute('INSERT INTO order_events(order_id,status,created_at) VALUES(?,?,?)',(order_id,new_status,datetime.now().strftime('%Y-%m-%d %H:%M')))

@app.post('/orders/<int:order_id>/advance')
@login_required()
def advance(order_id):
    role=session['role']
    if role not in ('staff','courier'): abort(403)
    with db() as con: order=con.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
    if not order: abort(404)
    transitions={'Шинэ':'Баталгаажсан','Баталгаажсан':'Бэлтгэж байна','Бэлтгэж байна':'Бэлэн болсон'} if role=='staff' else {'Бэлэн болсон':'Хүргэлтэд гарсан','Хүргэлтэд гарсан':'Хүргэгдсэн'}
    next_status=transitions.get(order['status'])
    if not next_status: abort(403)
    change_status(order_id,next_status); flash('Захиалгын төлөв шинэчлэгдлээ.','success'); return redirect(url_for('dashboard'))

@app.post('/orders/<int:order_id>/assign')
@login_required('admin')
def assign(order_id):
    raw=request.form.get('courier_id',''); cid=int(raw) if raw.isdigit() else None
    with db() as con:
        order=con.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
        courier=con.execute("SELECT id FROM users WHERE id=? AND role='courier' AND active=1",(cid,)).fetchone() if cid else None
        if not order or not order['delivery'] or not courier: abort(400)
        con.execute('UPDATE orders SET courier_id=? WHERE id=?',(cid,order_id))
    flash('Хүргэлтийн ажилтан хуваариллаа.','success'); return redirect(url_for('dashboard'))

@app.post('/orders/<int:order_id>/status')
@login_required('admin')
def admin_status(order_id):
    change_status(order_id,request.form.get('status','')); flash('Төлөв шинэчлэгдлээ.','success'); return redirect(url_for('dashboard'))

@app.post('/foods/add')
@login_required('admin')
def food_add():
    name=request.form.get('name','').strip(); desc=request.form.get('description','').strip(); category=request.form.get('category','').strip(); emoji=request.form.get('emoji','🍲').strip() or '🍲'
    try: price=int(request.form.get('price','0'))
    except ValueError: price=-1
    if not name or not category or price<0: flash('Хоолны мэдээллээ зөв оруулна уу.','error')
    else:
        with db() as con: con.execute('INSERT INTO foods(name,description,price,category,emoji) VALUES(?,?,?,?,?)',(name,desc,price,category,emoji))
        flash('Хоол нэмэгдлээ.','success')
    return redirect(url_for('dashboard'))

@app.post('/foods/<int:food_id>/toggle')
@login_required('admin')
def food_toggle(food_id):
    with db() as con: con.execute('UPDATE foods SET available=1-available WHERE id=?',(food_id,))
    return redirect(url_for('dashboard'))

@app.route('/admin/users',methods=['GET','POST'])
@login_required('admin')
def users():
    if request.method=='POST':
        name=request.form.get('name','').strip(); email=request.form.get('email','').strip().lower(); password=request.form.get('password',''); role=request.form.get('role','')
        if not name or '@' not in email or len(password)<8 or role not in ('staff','courier'):
            flash('Ажилтны мэдээлэл буруу байна.','error')
        else:
            try:
                with db() as con: con.execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',(name,email,generate_password_hash(password),role))
                flash('Ажилтны эрх үүсгэлээ.','success')
            except sqlite3.IntegrityError: flash('Имэйл давхцаж байна.','error')
        return redirect(url_for('users'))
    with db() as con: rows=con.execute('SELECT id,name,email,phone,role,active,last_login FROM users ORDER BY id DESC').fetchall()
    return render_template('users.html',users=rows)


@app.post('/admin/users/<int:user_id>/role')
@login_required('admin')
def user_role(user_id):
    new_role=request.form.get('role','')
    if new_role not in ROLES: abort(400)
    if user_id==session['uid']:
        flash('Өөрийн админ эрхийг өөрчлөх боломжгүй.','error'); return redirect(url_for('users'))
    with db() as con:
        target=con.execute('SELECT id FROM users WHERE id=?',(user_id,)).fetchone()
        if not target: abort(404)
        con.execute('UPDATE users SET role=? WHERE id=?',(new_role,user_id))
    flash('Хэрэглэгчийн эрх шинэчлэгдлээ.','success')
    return redirect(url_for('users'))

@app.post('/admin/users/<int:user_id>/active')
@login_required('admin')
def user_active(user_id):
    if user_id==session['uid']:
        flash('Өөрийн бүртгэлийг идэвхгүй болгох боломжгүй.','error'); return redirect(url_for('users'))
    with db() as con:
        target=con.execute('SELECT active FROM users WHERE id=?',(user_id,)).fetchone()
        if not target: abort(404)
        con.execute('UPDATE users SET active=? WHERE id=?',(0 if target['active'] else 1,user_id))
    flash('Хандалтын төлөв шинэчлэгдлээ.','success')
    return redirect(url_for('users'))

if __name__=='__main__':
    init_db()
    app.run(debug=True,port=5001)
