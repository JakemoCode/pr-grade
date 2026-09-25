# The check and the write share one synchronous unit of work, so nothing can land between them.
class Runtime:
    def __init__(self, db, store):
        self.db, self.store = db, store

    def record_return(self, execution_id):
        with self.db.unit_of_work():
            execution = self.store.require(execution_id)
            if execution.status != 'running':
                return 'rejected'
            self.store.append(execution_id, 'returned')
            return 'accepted'
