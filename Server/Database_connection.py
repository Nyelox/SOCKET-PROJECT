import pymysql
from Server.auth import userauth

auth = userauth()



def handle_login(username, password):
    # Check if account is locked
    locked, lock_message = auth.is_locked(username)
    if locked:
        return {"status": "error", "message": lock_message}

    con = pymysql.connect(
        host='localhost',
        user='root',
        password='1234',
        database='userdata',
    )
    cursor = con.cursor()

    # Get password, is_blocked, and is_admin
    # We use Try/Except or check columns exists, but assuming we will add them.
    # To be safe against "column not found" before migration, we might want to just fetch everything or separate queries.
    # However, I will rely on the server_app to migrate the DB on startup.
    try:
        query = 'SELECT password, is_blocked, is_admin FROM data WHERE username=%s'
        cursor.execute(query, (username,))
        result = cursor.fetchone()
    except Exception:
        # Fallback if columns don't exist yet (though we should migrate first)
        query = 'SELECT password FROM data WHERE username=%s'
        cursor.execute(query, (username,))
        result = cursor.fetchone()
        if result:
            result = (result[0], 0, 0) # Default to not blocking and not admin

    con.close()

    if result:
        stored_hash = result[0]
        # Handle potential tuple format differences if fallback was used
        is_blocked = result[1] if len(result) > 1 else 0
        is_admin = result[2] if len(result) > 2 else 0

        if isinstance(stored_hash, str):
            stored_hash = stored_hash.encode('utf-8')
        
        if auth.check_password(password, stored_hash):
            if username in auth.login_attempts:
                del auth.login_attempts[username]
            
            if is_blocked:
                 return {"status": "error", "message": "Account is blocked by admin", "is_blocked": True}

            return {
                "status": "success", 
                "message": "Login successful", 
                "is_admin": bool(is_admin),
                "is_blocked": bool(is_blocked)
            }
        else:
            auth.track_failed_attempt(username)
            return {"status": "error", "message": "Invalid username or password"}
    else:
        auth.track_failed_attempt(username)
        return {"status": "error", "message": "Invalid username or password"}


def handle_signup(username, password):
    con = pymysql.connect(
        host='localhost',
        user='root',
        password='1234',
        database='userdata',
    )
    mycursor = con.cursor()

    mycursor.execute('SELECT * FROM data WHERE username=%s', (username,))
    if mycursor.fetchone():
        response = {"status": "error", "message": "Username already exists"}
    else:
        pwd_hash = auth.hash_password(password)

        if isinstance(pwd_hash, bytes):
            pwd_hash = pwd_hash.decode('utf-8')

        # Insert with defaults for is_blocked and is_admin
        # If columns don't exist, this might fail unless we update schema first.
        # We will assume schema is updated by server_app.py init_db OR use safer insert.
        # Let's try to insert defaults if columns exist, or fallback.
        # Actually simplest is to ensure columns exist via server_app.py before this runs.
        try:
             mycursor.execute('INSERT INTO data(username, password, is_blocked, is_admin) VALUES (%s, %s, 0, 0)', (username, pwd_hash))
        except Exception:
             # Fallback if columns missing
             mycursor.execute('INSERT INTO data(username, password) VALUES (%s, %s)', (username, pwd_hash))
             
        con.commit()
        response = {"status": "success", "message": "Sign Up successful"}

    con.close()
    return response
