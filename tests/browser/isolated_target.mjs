/* Isolated-instance target for the affected 03F regression specs.
 *
 * Every variable is required, so a spec can never silently connect to the local
 * dev instance (port 8000) or the protected acceptance database. GEP_DEV_INSTANCE=1
 * is the explicit opt-in for the maintainer's own dev instance only; it is never
 * used by the 03F acceptance orchestrator.
 */
import fs from 'node:fs';

export function isolated() {
  const env = process.env;
  if (env.GEP_ISO_ADMIN_URL && env.GEP_ISO_EXPERIMENT_URL && env.GEP_ISO_USERNAME && env.GEP_ISO_PASSWORD) {
    return {
      admin: env.GEP_ISO_ADMIN_URL,
      experiment: env.GEP_ISO_EXPERIMENT_URL,
      credentials: {username: env.GEP_ISO_USERNAME, password: env.GEP_ISO_PASSWORD},
      connection: env.GEP_ISO_CONNECTION || null,
      db: env.GEP_ISO_DB || null,
      descriptor: env.GEP_ISO_DESCRIPTOR || 'build/native/descriptor.json',
      web_zip: env.GEP_ISO_WEB_ZIP || 'build/synthetic_web.zip',
      native_app: env.GEP_ISO_NATIVE_APP || null,
      scratch: env.GEP_ISO_SCRATCH || null,
      run_url_file: env.GEP_ISO_RUN_URL_FILE || 'build/web/run_url.txt',
      study_url_file: env.GEP_ISO_STUDY_URL_FILE || 'build/web/study_url.txt',
    };
  }
  if (env.GEP_DEV_INSTANCE === '1') {
    return {
      admin: 'http://admin.localhost:8000',
      experiment: 'http://experiment.localhost:8000',
      credentials: JSON.parse(fs.readFileSync('local_data/dev_credentials.json', 'utf8')),
      connection: 'build/native/connection.json',
      db: 'local_data/gep.sqlite3',
      descriptor: 'build/native/descriptor.json',
      web_zip: 'build/synthetic_web.zip',
      run_url_file: 'build/web/run_url.txt',
      study_url_file: 'build/web/study_url.txt',
    };
  }
  throw new Error('set GEP_ISO_ADMIN_URL/GEP_ISO_EXPERIMENT_URL/GEP_ISO_USERNAME/GEP_ISO_PASSWORD for an isolated ' +
    'instance, or GEP_DEV_INSTANCE=1 to use the local dev instance explicitly');
}

export function connectionConfig(target) {
  if (!target.connection) throw new Error('GEP_ISO_CONNECTION is required for this spec');
  return JSON.parse(fs.readFileSync(target.connection, 'utf8'));
}
