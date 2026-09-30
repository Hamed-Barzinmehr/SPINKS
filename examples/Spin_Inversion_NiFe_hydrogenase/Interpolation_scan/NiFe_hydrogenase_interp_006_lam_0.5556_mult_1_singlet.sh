#!/bin/bash
#PBS -N NiFe_hydrogenase_interp_006_lam_0.5556_mult_1_singlet
#PBS -l nodes=1:ppn=12
#PBS -o NiFe_hydrogenase_interp_006_lam_0.5556_mult_1_singlet.pbs.out
#PBS -e NiFe_hydrogenase_interp_006_lam_0.5556_mult_1_singlet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: NiFe_hydrogenase_interp_006_lam_0.5556_mult_1_singlet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca NiFe_hydrogenase_interp_006_lam_0.5556_mult_1_singlet.inp > NiFe_hydrogenase_interp_006_lam_0.5556_mult_1_singlet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
