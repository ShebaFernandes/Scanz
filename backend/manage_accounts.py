"""Trusted local administration. Never exposed as an HTTP route."""
import argparse
import time
try:
    from . import main
except ImportError:
    import main

def run():
    parser=argparse.ArgumentParser(description='Local account administration; no model or Paytm calls.')
    sub=parser.add_subparsers(dest='command',required=True)
    promote=sub.add_parser('promote-operator');promote.add_argument('email')
    assignment=sub.add_parser('assign-case');assignment.add_argument('email');assignment.add_argument('case_id')
    bind=sub.add_parser('bind-paytm');bind.add_argument('email');bind.add_argument('order_id');bind.add_argument('provider_order_id');bind.add_argument('--refund-id')
    args=parser.parse_args();main.initialize()
    with main.connection() as db:
        db.execute('BEGIN IMMEDIATE');user=db.execute('SELECT * FROM auth_users WHERE email=?',(args.email.strip().lower(),)).fetchone()
        if not user:parser.error('Create this account in the app first.')
        if args.command=='promote-operator':
            db.execute("UPDATE auth_users SET role='operator' WHERE id=?",(user['id'],));db.execute('DELETE FROM auth_sessions WHERE user_id=?',(user['id'],))
            main.auth.audit(db,'operator_assigned_locally',uid=user['id']);print('Staff role assigned. Existing sessions were revoked; sign in again.')
        elif args.command=='assign-case':
            if user['role']!='operator':parser.error('Assign only to an operator account.')
            case=db.execute('SELECT order_id FROM cases WHERE id=?',(args.case_id,)).fetchone()
            if not case:parser.error('Case not found.')
            db.execute('INSERT OR IGNORE INTO support_assignments VALUES (?,?)',(case['order_id'],user['id']))
            main.auth.audit(db,'case_assigned_locally',args.case_id,user['id']);print('This one case is assigned to the operator. Other customer orders remain inaccessible.')
        else:
            order=db.execute('SELECT * FROM orders WHERE id=? AND customer=?',(args.order_id,user['id'])).fetchone()
            if not order:parser.error('Order does not belong to this account.')
            mid=main.paytm_mcp.configuration()['mid']
            if not mid:parser.error('Configure PAYTM_MERCHANT_ID server-side first.')
            db.execute('INSERT INTO paytm_bindings VALUES (?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET merchant_id=excluded.merchant_id,provider_order_id=excluded.provider_order_id,provider_refund_id=excluded.provider_refund_id',(args.order_id,mid,args.provider_order_id,args.refund_id))
            main.auth.audit(db,'paytm_binding_configured_locally',args.order_id,user['id']);print('Trusted provider binding saved. No Paytm request or financial action was made.')
if __name__=='__main__':run()
