"""Explicit, re-entrant study-deletion cleanup (R00 §D).

No resident daemon: an operator runs this bounded maintenance command, which
advances every unfinished job from its persisted state/cursor. A process that
dies mid-batch leaves the job in ``marked``/``cleaning`` with the last committed
cursor, so the next run continues instead of claiming success; a failed file
step leaves ``failed`` with the error code until a later run retries it.
"""
from django.core.management.base import BaseCommand, CommandError

from core import deletion


class Command(BaseCommand):
    help = ('Process pending study deletion jobs in bounded, resumable batches '
            '(state and cursor are persisted; complete is only written after the '
            'database rows and private files are verified gone).')

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=deletion.DEFAULT_BATCH_SIZE,
                            help='Database rows and real file removals processed per bounded '
                                 'batch (default: %(default)s).')
        parser.add_argument('--max-batches', type=int, default=0,
                            help='Stop after this many batches per job; 0 means until done/failed.')
        parser.add_argument('--study', type=str, default='',
                            help='Optional study UUID: only process that deletion job.')

    def handle(self, *args, **options):
        batch_size = int(options['batch_size'])
        if batch_size < 1:
            raise CommandError('--batch-size must be a positive integer')
        max_batches = int(options['max_batches'])
        if max_batches < 0:
            raise CommandError('--max-batches must be zero (until done) or a positive integer')
        max_batches = max_batches or None
        study_uuid = (options['study'] or '').strip() or None
        results = deletion.cleanup_pending(batch_size=batch_size, max_batches=max_batches,
                                           study_uuid=study_uuid)
        failed = 0
        for result in results:
            if result['state'] == 'failed':
                failed += 1
                self.stderr.write(
                    f"study {result.get('study_uuid')}: state={result['state']} "
                    f"cursor={result['cursor']} error={result['error_code']} "
                    f"files_remaining={result['files_remaining']}")
            else:
                self.stdout.write(
                    f"study {result.get('study_uuid')}: state={result['state']} "
                    f"cursor={result['cursor']} batches={result['batches']}")
        if not results:
            self.stdout.write('no pending study deletion jobs')
        if failed:
            raise SystemExit(1)
