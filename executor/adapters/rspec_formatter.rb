# hh's RSpec formatter: one ledger row per finished example.
#
# RSpec's own `--format json` is written when the process ends, so a suite
# killed at hh's budget would report nothing. A formatter is called as each
# example finishes, which is exactly the granularity the ledger needs.
#
# Loaded with `--require <this file> --format HhRspecFormatter`; the ledger path
# comes from HH_RSPEC_LEDGER. `key_for` must agree with
# executor/adapters/rspec_stream.py::key_for — tests/test_systems_adapters.py
# pins the Python side and the integration test compares the two on real ids.

require 'json'

class HhRspecFormatter
  RSpec::Core::Formatters.register self,
    :example_passed, :example_failed, :example_pending, :close

  def initialize(_output)
    @ledger = ENV['HH_RSPEC_LEDGER']
    @count = 0
  end

  def example_passed(notification)
    row(notification.example, 'passed')
  end

  def example_failed(notification)
    row(notification.example, 'failed')
  end

  def example_pending(notification)
    row(notification.example, 'skipped')
  end

  def close(_notification)
    write({ type: 'suite_end', tests: @count, ts: Time.now.to_f })
  end

  private

  def key_for(id)
    path, suffix = id.split('[', 2)
    suffix = suffix.to_s.sub(/\]\z/, '')
    path = path.sub(%r{\A\./}, '')
    "rspec::#{path}/#{suffix}"
  end

  def row(example, status)
    run_time = example.execution_result.run_time
    ms = (run_time.to_f * 1000.0 * 1000.0).round / 1000.0
    write({
      type: 'end',
      key: key_for(example.id),
      raw_id: example.id,
      status: status,
      duration_ms: ms,
      censored: false,
      ts: Time.now.to_f
    })
    @count += 1
  end

  def write(row)
    File.open(@ledger, 'a') { |f| f.write(JSON.generate(row) + "\n") }
  end
end
