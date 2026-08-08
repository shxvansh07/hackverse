'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api, TriageCase, Prescription, Medication, Appointment, ReferralInfo } from '@/lib/api';

export default function DoctorDashboard() {
  const router = useRouter();
  const [cases, setCases] = useState<TriageCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedRiskFilter, setSelectedRiskFilter] = useState<string>('');
  const [selectedStatusFilter, setSelectedStatusFilter] = useState<string>('');
  const [wsConnected, setWsConnected] = useState(false);

  const [activeCase, setActiveCase] = useState<TriageCase | null>(null);
  const [activeDraft, setActiveDraft] = useState<Prescription | null>(null);
  const [activeAppointment, setActiveAppointment] = useState<Appointment | null>(null);
  const [activeReferral, setActiveReferral] = useState<ReferralInfo | null>(null);

  const [editMedications, setEditMedications] = useState<Medication[]>([]);
  const [editInstructions, setEditInstructions] = useState<string>('');
  const [doctorNotes, setDoctorNotes] = useState<string>('');

  // Doctor Referral & Offline Appointment Form State
  const [referralSpecialty, setReferralSpecialty] = useState<string>('Cardiology');
  const [referralNotes, setReferralNotes] = useState<string>('');
  const [offlineTime, setOfflineTime] = useState<string>('');
  const [offlineLocation, setOfflineLocation] = useState<string>('Main Hospital OPD Clinic, Room 102');

  const [actionLoading, setActionLoading] = useState(false);
  const [decisionSuccessMsg, setDecisionSuccessMsg] = useState<string>('');

  useEffect(() => {
    if (!api.isDoctorAuthenticated()) {
      router.push('/doctor/login');
    }
  }, []);

  const fetchCases = async () => {
    setLoading(true);
    try {
      const list = await api.getDoctorCases(selectedRiskFilter, selectedStatusFilter);
      setCases(list);
    } catch (err) {
      console.error('Fetch cases error:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCases();
  }, [selectedRiskFilter, selectedStatusFilter]);

  useEffect(() => {
    const wsUrl = (process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000').replace('http', 'ws') + '/api/ws/doctor';
    let socket: WebSocket;

    try {
      socket = new WebSocket(wsUrl);
      socket.onopen = () => setWsConnected(true);
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event === 'NEW_CASE_UPDATE' || data.event === 'CASE_DECISION_UPDATED') {
            fetchCases();
          }
        } catch (e) {
          console.error('WebSocket error:', e);
        }
      };
      socket.onclose = () => setWsConnected(false);
      socket.onerror = () => setWsConnected(false);
    } catch (err) {
      console.error('WebSocket connection error:', err);
    }

    return () => {
      if (socket) socket.close();
    };
  }, []);

  const handleLogout = () => {
    api.doctorLogout();
    router.push('/doctor/login');
  };

  const openCaseDetail = async (c: TriageCase) => {
    setDecisionSuccessMsg('');
    try {
      const detail = await api.getCaseDetail(c.case_id);
      setActiveCase(detail.case || c);
      setActiveDraft(detail.prescription_draft);
      setActiveAppointment(detail.appointment || null);
      setActiveReferral(detail.referral || null);

      if (detail.prescription_draft) {
        setEditMedications(detail.prescription_draft.medications || []);
        setEditInstructions(detail.prescription_draft.instructions || '');
      } else {
        setEditMedications([]);
        setEditInstructions('');
      }
    } catch (err) {
      console.error('Fetch detail error:', err);
      setActiveCase(c);
    }
  };

  const handleMedChange = (index: number, field: keyof Medication, val: string) => {
    const updated = [...editMedications];
    updated[index] = { ...updated[index], [field]: val };
    setEditMedications(updated);
  };

  const addMedicationRow = () => {
    setEditMedications([
      ...editMedications,
      { name: '', dosage: '', frequency: '', duration: '', instructions: '' }
    ]);
  };

  const removeMedicationRow = (index: number) => {
    setEditMedications(editMedications.filter((_, i) => i !== index));
  };

  const handleDecision = async (decision: 'APPROVE' | 'MODIFY' | 'REJECT' | 'NEEDS_REVIEW' | 'REFERRAL' | 'OFFLINE_APPOINTMENT') => {
    if (!activeCase) return;
    setActionLoading(true);
    try {
      await api.submitDoctorDecision(
        activeCase.case_id,
        decision,
        doctorNotes,
        editMedications,
        editInstructions,
        referralSpecialty,
        referralNotes,
        offlineTime,
        offlineLocation
      );
      setDecisionSuccessMsg(`Decision '${decision}' submitted successfully.`);
      fetchCases();
      setTimeout(() => {
        setActiveCase(null);
      }, 1200);
    } catch (err) {
      console.error('Decision error:', err);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-white text-black flex flex-col justify-between p-4 sm:p-8">
      {/* Header */}
      <header className="max-w-7xl mx-auto w-full flex justify-between items-center py-4 px-6 bg-neutral-50 rounded border border-neutral-300 mb-6">
        <div>
          <h1 className="text-base font-bold text-black">Physician Clinical Review Dashboard</h1>
          <p className="text-xs text-neutral-600 font-mono">Authenticated Doctor Review Portal (All Details in English)</p>
        </div>

        <div className="flex items-center space-x-3 text-xs font-mono">
          <span className="text-neutral-600">[Live Queue Sync: {wsConnected ? 'ONLINE' : 'OFFLINE'}]</span>
          <button onClick={handleLogout} className="text-neutral-600 hover:text-black font-bold">
            [Sign Out]
          </button>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto w-full flex-1 space-y-6">
        {/* Metrics Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-4 text-xs font-mono">
          <div className="bg-neutral-50 p-4 rounded border border-neutral-300">
            <div className="text-neutral-600">Total Cases</div>
            <div className="text-xl font-bold text-black mt-1">{cases.length}</div>
          </div>
          <div className="bg-neutral-50 p-4 rounded border border-neutral-300">
            <div className="text-neutral-600">Low Risk Cases</div>
            <div className="text-xl font-bold text-black mt-1">{cases.filter(c => c.triage_status === 'LOW_RISK').length}</div>
          </div>
          <div className="bg-neutral-50 p-4 rounded border border-neutral-300">
            <div className="text-neutral-600">Urgent Escalations</div>
            <div className="text-xl font-bold text-black mt-1">{cases.filter(c => c.triage_status === 'URGENT').length}</div>
          </div>
          <div className="bg-neutral-50 p-4 rounded border border-neutral-300">
            <div className="text-neutral-600">Pending Review</div>
            <div className="text-xl font-bold text-black mt-1">{cases.filter(c => c.review_status === 'PENDING').length}</div>
          </div>
        </div>

        {/* Filter Bar */}
        <div className="bg-neutral-50 p-3 rounded border border-neutral-300 flex justify-between items-center text-xs">
          <span className="font-bold text-black">Filter Queue:</span>
          <select
            value={selectedRiskFilter}
            onChange={(e) => setSelectedRiskFilter(e.target.value)}
            className="bg-white border border-neutral-300 text-black rounded px-2.5 py-1.5 focus:outline-none font-mono"
          >
            <option value="">All Triage Risk Levels</option>
            <option value="LOW_RISK">Low Risk</option>
            <option value="UNCERTAIN">Uncertain</option>
            <option value="URGENT">Urgent Red Flag</option>
          </select>
        </div>

        {/* Queue Table */}
        <div className="bg-white rounded border border-neutral-300 overflow-hidden">
          <div className="p-3 border-b border-neutral-300 bg-neutral-50 flex justify-between items-center text-xs font-mono">
            <span className="font-bold text-black">Active Patient Intake Cases</span>
            <button onClick={fetchCases} className="text-neutral-600 hover:text-black font-bold">[Refresh Queue]</button>
          </div>

          {loading ? (
            <div className="p-8 text-center text-xs text-neutral-500 font-mono">Loading patient queue...</div>
          ) : cases.length === 0 ? (
            <div className="p-8 text-center text-xs text-neutral-500 font-mono">No active cases found.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs text-black">
                <thead className="bg-neutral-100 text-neutral-700 uppercase text-[10px] border-b border-neutral-300 font-mono">
                  <tr>
                    <th className="p-3">Case ID</th>
                    <th className="p-3">Patient ID</th>
                    <th className="p-3">Primary Complaint & English Clinical Summary</th>
                    <th className="p-3">Triage Risk</th>
                    <th className="p-3">Review Status</th>
                    <th className="p-3 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-200">
                  {cases.map((c) => (
                    <tr key={c.case_id} className="hover:bg-neutral-50">
                      <td className="p-3 font-mono font-bold text-black">{c.case_id}</td>
                      <td className="p-3 font-mono text-neutral-600">{c.patient_id}</td>
                      <td className="p-3 max-w-md">
                        <div className="font-bold text-black uppercase text-[11px]">{c.symptoms.join(', ') || 'General Consultation'}</div>
                        <div className="text-neutral-600 text-[11px] line-clamp-2">{c.summary_en}</div>
                      </td>
                      <td className="p-3 font-mono font-bold">
                        <span className={c.triage_status === 'URGENT' ? 'text-red-600 font-bold' : 'text-black'}>
                          [{c.triage_status}]
                        </span>
                      </td>
                      <td className="p-3 font-mono font-semibold">[{c.review_status}]</td>
                      <td className="p-3 text-right">
                        <button
                          onClick={() => openCaseDetail(c)}
                          className="px-3 py-1.5 bg-black text-white font-bold rounded text-[11px] hover:bg-neutral-800"
                        >
                          Review Case
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>

      {/* Comprehensive Case Review Modal */}
      {activeCase && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-white max-w-4xl w-full rounded border border-neutral-400 p-6 space-y-5 max-h-[90vh] overflow-y-auto my-auto text-xs text-black shadow-2xl">
            {/* Modal Header */}
            <div className="flex justify-between items-start border-b border-neutral-300 pb-3">
              <div>
                <h3 className="text-base font-bold text-black flex items-center space-x-2">
                  <span>Clinical Case Review — {activeCase.case_id}</span>
                </h3>
                <div className="text-neutral-600 font-mono text-[11px]">
                  Patient ID: <strong>{activeCase.patient_id}</strong> | Triage Risk: <strong>[{activeCase.triage_status}]</strong> | Status: <strong>[{activeCase.review_status}]</strong>
                </div>
              </div>
              <button onClick={() => setActiveCase(null)} className="text-neutral-600 hover:text-black text-sm font-mono font-bold">[Close X]</button>
            </div>

            {decisionSuccessMsg && (
              <div className="bg-neutral-100 border border-black text-black p-3 rounded font-mono font-bold">
                ✓ {decisionSuccessMsg}
              </div>
            )}

            {/* Structured Clinical Profile Card */}
            <div className="bg-neutral-50 p-4 rounded border border-neutral-300 space-y-2 text-xs">
              <div className="font-bold uppercase text-black text-xs border-b border-neutral-200 pb-1">
                📋 English Clinical Summary & Structured Findings
              </div>
              <p className="text-black leading-relaxed font-medium">{activeCase.summary_en}</p>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 text-[11px] font-mono border-t border-neutral-200">
                <div><strong>Symptoms:</strong> {activeCase.symptoms.join(', ') || 'General Consultation'}</div>
                <div><strong>Duration:</strong> {activeCase.duration || 'Recent onset'}</div>
                <div><strong>Severity:</strong> {activeCase.severity || 'Mild'}</div>
                <div><strong>Red Flags:</strong> {activeCase.red_flags.join(', ') || 'None'}</div>
              </div>
              <div className="grid grid-cols-2 gap-2 text-[11px] font-mono">
                <div><strong>Medical History:</strong> {activeCase.medical_history.join('; ') || 'None reported'}</div>
                <div><strong>Allergies:</strong> {activeCase.allergies.join('; ') || 'None reported'}</div>
              </div>
            </div>

            {/* FULL PATIENT CONVERSATION TRANSCRIPT SECTION */}
            <div className="bg-neutral-50 p-4 rounded border border-neutral-300 space-y-2">
              <div className="font-bold uppercase text-black text-xs border-b border-neutral-200 pb-1">
                💬 Full Patient-AI Intake Conversation Transcript
              </div>
              <div className="max-h-48 overflow-y-auto space-y-2 pr-1 font-mono text-[11px]">
                {activeCase.transcript && activeCase.transcript.length > 0 ? (
                  activeCase.transcript.map((msg, idx) => (
                    <div key={idx} className={`p-2 rounded border ${msg.sender === 'patient' ? 'bg-white border-neutral-300 text-black' : 'bg-neutral-100 border-neutral-200 text-neutral-800'}`}>
                      <span className="font-bold uppercase">{msg.sender === 'patient' ? 'Patient' : 'AI Assistant'}: </span>
                      <span>{msg.text}</span>
                    </div>
                  ))
                ) : (
                  <div className="text-neutral-500 italic">No transcript recorded for this session.</div>
                )}
              </div>
            </div>

            {/* Existing Appointment or Referral Records */}
            {activeAppointment && (
              <div className="bg-neutral-100 p-3 rounded border border-black font-mono text-xs space-y-1">
                <div className="font-bold text-black uppercase">[APPOINTMENT SLOT CONFIRMED]</div>
                <div><strong>Slot Time:</strong> {activeAppointment.slot_time}</div>
                <div><strong>Location:</strong> {activeAppointment.clinic_location}</div>
                <div><strong>Type:</strong> {activeAppointment.type}</div>
              </div>
            )}

            {activeReferral && (
              <div className="bg-neutral-100 p-3 rounded border border-black font-mono text-xs space-y-1">
                <div className="font-bold text-black uppercase">[SPECIALIST REFERRAL ISSUED]</div>
                <div><strong>Department:</strong> {activeReferral.specialty}</div>
                <div><strong>Clinical Notes:</strong> {activeReferral.referral_notes}</div>
                <div><strong>Issued By:</strong> {activeReferral.doctor_name}</div>
              </div>
            )}

            {/* AI Draft Prescription Editor */}
            <div className="bg-neutral-50 p-4 rounded border border-neutral-300 space-y-3">
              <div className="flex justify-between items-center border-b border-neutral-300 pb-2">
                <span className="font-bold uppercase text-black text-xs">AI Draft Prescription Editor</span>
                <button onClick={addMedicationRow} className="text-black font-bold font-mono text-[11px]">[+ Add Medication]</button>
              </div>

              <div className="space-y-2">
                {editMedications.map((med, idx) => (
                  <div key={idx} className="grid grid-cols-1 sm:grid-cols-5 gap-2 items-center">
                    <input
                      type="text"
                      value={med.name}
                      onChange={(e) => handleMedChange(idx, 'name', e.target.value)}
                      placeholder="Medication Name"
                      className="bg-white border border-neutral-300 rounded p-1.5 text-black text-xs"
                    />
                    <input
                      type="text"
                      value={med.dosage}
                      onChange={(e) => handleMedChange(idx, 'dosage', e.target.value)}
                      placeholder="Dosage (e.g. 500mg)"
                      className="bg-white border border-neutral-300 rounded p-1.5 text-black text-xs"
                    />
                    <input
                      type="text"
                      value={med.frequency}
                      onChange={(e) => handleMedChange(idx, 'frequency', e.target.value)}
                      placeholder="Frequency (e.g. Twice daily)"
                      className="bg-white border border-neutral-300 rounded p-1.5 text-black text-xs"
                    />
                    <input
                      type="text"
                      value={med.duration}
                      onChange={(e) => handleMedChange(idx, 'duration', e.target.value)}
                      placeholder="Duration (e.g. 3 days)"
                      className="bg-white border border-neutral-300 rounded p-1.5 text-black text-xs"
                    />
                    <div className="flex items-center space-x-1">
                      <input
                        type="text"
                        value={med.instructions}
                        onChange={(e) => handleMedChange(idx, 'instructions', e.target.value)}
                        placeholder="Instructions"
                        className="bg-white border border-neutral-300 rounded p-1.5 text-black text-xs flex-1"
                      />
                      <button onClick={() => removeMedicationRow(idx)} className="text-neutral-600 hover:text-black p-1 font-bold">[X]</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Doctor Decision & Referral Form */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2">
              {/* Option A: Specialist Referral */}
              <div className="bg-neutral-50 p-3 rounded border border-neutral-300 space-y-2">
                <div className="font-bold text-black uppercase text-[11px]">Option 1: Refer to Specialist</div>
                <select
                  value={referralSpecialty}
                  onChange={(e) => setReferralSpecialty(e.target.value)}
                  className="w-full bg-white border border-neutral-300 rounded p-1.5 text-xs text-black"
                >
                  <option value="Cardiology">Cardiology (Heart)</option>
                  <option value="ENT">ENT (Ear, Nose, Throat)</option>
                  <option value="Neurology">Neurology</option>
                  <option value="Orthopedics">Orthopedics (Bones)</option>
                  <option value="Gastroenterology">Gastroenterology (Stomach)</option>
                  <option value="Dermatology">Dermatology (Skin)</option>
                  <option value="General Surgery">General Surgery</option>
                  <option value="Pulmonology">Pulmonology (Lungs)</option>
                </select>
                <input
                  type="text"
                  value={referralNotes}
                  onChange={(e) => setReferralNotes(e.target.value)}
                  placeholder="Referral clinical reason..."
                  className="w-full bg-white border border-neutral-300 rounded p-1.5 text-xs text-black"
                />
                <button
                  disabled={actionLoading}
                  onClick={() => handleDecision('REFERRAL')}
                  className="w-full py-1.5 bg-black text-white font-bold rounded text-xs hover:bg-neutral-800"
                >
                  Issue Specialist Referral
                </button>
              </div>

              {/* Option B: Schedule In-Person Offline Appointment */}
              <div className="bg-neutral-50 p-3 rounded border border-neutral-300 space-y-2">
                <div className="font-bold text-black uppercase text-[11px]">Option 2: Schedule In-Person Appointment</div>
                <input
                  type="text"
                  value={offlineTime}
                  onChange={(e) => setOfflineTime(e.target.value)}
                  placeholder="Date & Time (e.g. 2026-08-09 11:00 AM)"
                  className="w-full bg-white border border-neutral-300 rounded p-1.5 text-xs text-black"
                />
                <input
                  type="text"
                  value={offlineLocation}
                  onChange={(e) => setOfflineLocation(e.target.value)}
                  placeholder="Clinic Location (e.g. Room 102)"
                  className="w-full bg-white border border-neutral-300 rounded p-1.5 text-xs text-black"
                />
                <button
                  disabled={actionLoading}
                  onClick={() => handleDecision('OFFLINE_APPOINTMENT')}
                  className="w-full py-1.5 bg-black text-white font-bold rounded text-xs hover:bg-neutral-800"
                >
                  Schedule In-Person Appointment
                </button>
              </div>
            </div>

            {/* Standard Decision Actions */}
            <div className="flex justify-end space-x-2 pt-3 border-t border-neutral-300 font-mono">
              <button
                disabled={actionLoading}
                onClick={() => handleDecision('REJECT')}
                className="px-3 py-1.5 border border-neutral-400 text-neutral-700 rounded hover:bg-neutral-100"
              >
                Reject Case
              </button>
              <button
                disabled={actionLoading}
                onClick={() => handleDecision('MODIFY')}
                className="px-3 py-1.5 border border-black text-black rounded hover:bg-neutral-100 font-bold"
              >
                Save & Approve Modified
              </button>
              <button
                disabled={actionLoading}
                onClick={() => handleDecision('APPROVE')}
                className="px-4 py-1.5 bg-black text-white font-bold rounded hover:bg-neutral-800"
              >
                Approve Draft Prescription
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
