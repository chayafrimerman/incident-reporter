import pyodbc

try:

    conn = pyodbc.connect(

        'DRIVER={ODBC Driver 17 for SQL Server};'

        'SERVER=20.119.49.48,1433;'

        'DATABASE=OPUS2;'

        'UID=admin_chaya;'

        'PWD=Ch@ya0pu$#2026!Qx7^Lm92;'

        'Encrypt=yes;'

        'TrustServerCertificate=yes;'

        'Connection Timeout=5;'

    )

    print('Connected!')

    cursor = conn.cursor()

    cursor.execute('SELECT TOP 3 full_name FROM employee')

    print(cursor.fetchall())

except Exception as e:

    print('Error:', e)